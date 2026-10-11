#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { InventoryApiStack } from '../lib/inventory-api-stack';
import { parseStage } from '../lib/stage';

const app = new App();
const stage = parseStage(app.node.tryGetContext('stage') ?? 'ci');

new InventoryApiStack(app, stage, {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'ap-southeast-2',
  },
});
