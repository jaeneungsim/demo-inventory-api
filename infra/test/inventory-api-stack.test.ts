import { readFileSync } from 'node:fs';
import * as path from 'node:path';
import { App } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { InventoryApiStack } from '../lib/inventory-api-stack';
import { parseStage } from '../lib/stage';

const cdkContext = JSON.parse(
  readFileSync(path.join(__dirname, '..', 'cdk.json'), 'utf8'),
).context;

function synth(stage: string): Template {
  const app = new App({ context: { ...cdkContext, stage } });
  const stack = new InventoryApiStack(app, parseStage(stage), {
    env: { region: 'ap-southeast-2' },
  });
  return Template.fromStack(stack);
}

describe.each([
  { stage: 'inv-123', stackName: 'inv-api-preview-inv-123', retention: 3 },
  { stage: 'dev', stackName: 'inv-api-dev', retention: 14 },
])('stage $stage', ({ stage, stackName, retention }) => {
  const template = synth(stage);

  test('uses the names from the contract', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: `${stackName}-handler`,
      Handler: 'inventory.handler.handler',
      Runtime: 'python3.13',
      Architectures: ['arm64'],
      MemorySize: 256,
      Timeout: 10,
    });
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: `${stackName}-items`,
      BillingMode: 'PAY_PER_REQUEST',
      KeySchema: [{ AttributeName: 'id', KeyType: 'HASH' }],
    });
    template.hasResourceProperties('AWS::Logs::LogGroup', {
      LogGroupName: `/aws/lambda/${stackName}-handler`,
      RetentionInDays: retention,
    });
    template.hasResourceProperties('AWS::ApiGatewayV2::Api', {
      Name: stackName,
      ProtocolType: 'HTTP',
      CorsConfiguration: { AllowOrigins: ['*'] },
    });
  });

  test('throttles the default stage', () => {
    template.hasResourceProperties('AWS::ApiGatewayV2::Stage', {
      StageName: '$default',
      AutoDeploy: true,
      DefaultRouteSettings: { ThrottlingRateLimit: 10, ThrottlingBurstLimit: 20 },
    });
  });

  test('exposes the outputs pr-triage reads', () => {
    for (const output of ['ApiUrl', 'FunctionName', 'LogGroupName', 'TableName']) {
      template.hasOutput(output, {});
    }
  });

  test('puts the permissions boundary on every role', () => {
    const roles = Object.values(template.findResources('AWS::IAM::Role'));
    expect(roles.length).toBeGreaterThan(0);
    for (const role of roles) {
      expect(JSON.stringify(role.Properties.PermissionsBoundary)).toContain(
        ':policy/inv-preview-boundary',
      );
    }
  });

  test('removes data and logs with the stack', () => {
    for (const type of ['AWS::DynamoDB::Table', 'AWS::Logs::LogGroup']) {
      for (const resource of Object.values(template.findResources(type))) {
        expect(resource.DeletionPolicy).toBe('Delete');
      }
    }
  });

  test('keeps every log group under the stack name', () => {
    const names = Object.values(template.findResources('AWS::Logs::LogGroup')).map(
      (resource) => resource.Properties.LogGroupName,
    );
    expect(names.sort()).toEqual([`/aws/lambda/${stackName}-handler`, `/aws/lambda/${stackName}-seed`]);
  });

  test('seeds items 41, 42 and 43', () => {
    const [seed] = Object.values(template.findResources('Custom::AWS'));
    const call = JSON.parse(seed.Properties.Create);
    const items = call.parameters.RequestItems[`${stackName}-items`].map(
      (request: { PutRequest: { Item: { id: { S: string }; stock: { N: string } } } }) => [
        request.PutRequest.Item.id.S,
        request.PutRequest.Item.stock.N,
      ],
    );
    expect(items).toEqual([
      ['41', '12'],
      ['42', '5'],
      ['43', '0'],
    ]);
  });

  test('needs no CDK bootstrap stack', () => {
    const json = template.toJSON();
    expect(json.Parameters?.BootstrapVersion).toBeUndefined();
    expect(json.Rules?.CheckBootstrapVersion).toBeUndefined();
  });
});

test('rejects stages outside the contract', () => {
  expect(() => parseStage('prod')).toThrow(/Invalid stage/);
  expect(() => parseStage('INV-123')).toThrow(/Invalid stage/);
  expect(parseStage('ci').stackName).toBe('inv-api-ci');
});
