import React from 'react';
import { Linking, Text } from 'react-native';
import { TradeStatus } from '../lib/api';
import { Button, Notice } from './ui';

/** A trade who hasn't finished on the website is told so — without any mention of plans or prices. */
export function SetupNotice({ status }: { status: TradeStatus }) {
  if (!status.needs_web_setup || !status.setup_message) return null;
  return (
    <Notice tone="paint" title="Finish setting up on the website">
      <Text style={{ fontSize: 15, lineHeight: 21, marginBottom: status.setup_url ? 10 : 0 }}>{status.setup_message}</Text>
      {status.setup_url ? (
        <Button title="Open the Level website" small icon="open-outline" onPress={() => Linking.openURL(status.setup_url!)} />
      ) : null}
    </Notice>
  );
}
