import { readFileSync } from 'node:fs';
import * as path from 'node:path';
import {
  CfnOutput,
  CliCredentialsStackSynthesizer,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import { CorsHttpMethod, HttpApi, HttpStage } from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import { AttributeType, BillingMode, Table } from 'aws-cdk-lib/aws-dynamodb';
import { Architecture, Code, Function, Runtime } from 'aws-cdk-lib/aws-lambda';
import { LogGroup, RetentionDays } from 'aws-cdk-lib/aws-logs';
import {
  AwsCustomResource,
  AwsCustomResourcePolicy,
  PhysicalResourceId,
} from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { StageInfo } from './stage';

/** Asset bucket owned by pr-triage's PreviewPlatform stack (see docs/contracts.md there). */
export const ASSETS_BUCKET = 'inv-preview-assets-${AWS::AccountId}-${AWS::Region}';

const REPO_ROOT = path.join(__dirname, '..', '..');

interface SeedItem {
  id: string;
  name: string;
  price: number;
  stock: number;
}

export function loadSeedItems(): SeedItem[] {
  return JSON.parse(readFileSync(path.join(REPO_ROOT, 'seed', 'items.json'), 'utf8'));
}

export class InventoryApiStack extends Stack {
  constructor(scope: Construct, info: StageInfo, props: StackProps = {}) {
    super(scope, info.stackName, {
      ...props,
      stackName: info.stackName,
      description: `Inventory API (${info.kind}: ${info.stage}) reviewed by pr-triage`,
      synthesizer: new CliCredentialsStackSynthesizer({
        fileAssetsBucketName: ASSETS_BUCKET,
        bucketPrefix: `${info.stackName}/`,
      }),
    });

    const name = info.stackName;
    const retention = info.kind === 'dev' ? RetentionDays.TWO_WEEKS : RetentionDays.THREE_DAYS;

    const table = new Table(this, 'Items', {
      tableName: `${name}-items`,
      partitionKey: { name: 'id', type: AttributeType.STRING },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const logGroup = new LogGroup(this, 'HandlerLogs', {
      logGroupName: `/aws/lambda/${name}-handler`,
      retention,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const handler = new Function(this, 'Handler', {
      functionName: `${name}-handler`,
      runtime: Runtime.PYTHON_3_13,
      architecture: Architecture.ARM_64,
      handler: 'inventory.handler.handler',
      code: Code.fromAsset(path.join(REPO_ROOT, 'src'), {
        exclude: ['**/__pycache__', '**/*.pyc'],
      }),
      memorySize: 256,
      timeout: Duration.seconds(10),
      environment: { TABLE_NAME: table.tableName },
      logGroup,
    });
    table.grantReadWriteData(handler);

    const api = new HttpApi(this, 'Api', {
      apiName: name,
      createDefaultStage: false,
      defaultIntegration: new HttpLambdaIntegration('HandlerIntegration', handler),
      corsPreflight: {
        allowOrigins: ['*'],
        allowMethods: [
          CorsHttpMethod.GET,
          CorsHttpMethod.POST,
          CorsHttpMethod.PATCH,
          CorsHttpMethod.OPTIONS,
        ],
        allowHeaders: ['content-type'],
        exposeHeaders: ['x-request-id'],
        maxAge: Duration.hours(1),
      },
    });
    const stage = new HttpStage(this, 'DefaultStage', {
      httpApi: api,
      stageName: '$default',
      autoDeploy: true,
      throttle: { rateLimit: 10, burstLimit: 20 },
    });

    const seed = new AwsCustomResource(this, 'Seed', {
      onCreate: {
        service: 'DynamoDB',
        action: 'batchWriteItem',
        parameters: {
          RequestItems: {
            [`${name}-items`]: loadSeedItems().map((item) => ({
              PutRequest: { Item: toAttributeMap(item) },
            })),
          },
        },
        physicalResourceId: PhysicalResourceId.of(`${name}-seed`),
      },
      policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: [table.tableArn] }),
      installLatestAwsSdk: false,
      logGroup: new LogGroup(this, 'SeedLogs', {
        logGroupName: `/aws/lambda/${name}-seed`,
        retention,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
    });
    seed.node.addDependency(table);

    new CfnOutput(this, 'ApiUrl', { value: stage.url });
    new CfnOutput(this, 'FunctionName', { value: handler.functionName });
    new CfnOutput(this, 'LogGroupName', { value: logGroup.logGroupName });
    new CfnOutput(this, 'TableName', { value: table.tableName });
  }
}

function toAttributeMap(item: SeedItem) {
  return {
    id: { S: item.id },
    name: { S: item.name },
    price: { N: String(item.price) },
    stock: { N: String(item.stock) },
  };
}
