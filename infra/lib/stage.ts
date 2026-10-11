export type StageKind = 'preview' | 'dev' | 'ci';

export interface StageInfo {
  readonly stage: string;
  readonly kind: StageKind;
  readonly stackName: string;
}

const TICKET_STAGE = /^inv-\d+$/;

/** Maps the `stage` context value to the stack name in the interface contract. */
export function parseStage(stage: string): StageInfo {
  if (stage === 'dev') {
    return { stage, kind: 'dev', stackName: 'inv-api-dev' };
  }
  if (stage === 'ci') {
    return { stage, kind: 'ci', stackName: 'inv-api-ci' };
  }
  if (TICKET_STAGE.test(stage)) {
    return { stage, kind: 'preview', stackName: `inv-api-preview-${stage}` };
  }
  throw new Error(`Invalid stage "${stage}". Use dev, ci or a ticket stage such as inv-123.`);
}
