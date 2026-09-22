import React, { useState } from 'react';
import { Alert, Text } from 'react-native';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { unregisterForPush } from '../lib/push';
import { Button, ErrorText, Field, Notice, Screen, Title } from '../components/ui';
import { colors } from '../lib/theme';

/**
 * Delete the account from inside the app (App Store guideline 5.1.1(v)).
 * Same as Settings → Close account on the website: open jobs are closed,
 * offers released, and name, email, phone and password are wiped.
 */
export default function DeleteAccount() {
  const { user, signOut } = useAuth();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function confirm() {
    if (!password) {
      setError('Enter your password to confirm it’s you.');
      return;
    }
    Alert.alert('Delete your account?', 'This can’t be undone.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete my account', style: 'destructive', onPress: remove },
    ]);
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await unregisterForPush();
      const res = await api.deleteAccount(password);
      Alert.alert('Account deleted', res.message || 'Your account is closed and your details have been deleted.');
      await signOut();
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <Screen keyboard>
      <Title>Delete your account</Title>
      <Notice tone="danger" title="This can’t be undone">
        <Text style={{ fontSize: 15, lineHeight: 22 }}>
          {user?.role === 'trade'
            ? 'You’ll stop getting jobs straight away, jobs waiting on you go to other trades, and your name, email, phone number and password are deleted.'
            : 'Any open jobs are closed (the trades are told), and your name, email, phone number and password are deleted.'}
          {' '}Past quotes and messages stay with the other person, without your details.
        </Text>
      </Notice>
      <Text style={{ fontSize: 15, color: colors.ink2, marginBottom: 16 }}>
        Signed in as {user?.email}. Enter your password to confirm.
      </Text>
      <ErrorText>{error}</ErrorText>
      <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry textContentType="password" />
      <Button title="Delete my account" variant="danger" onPress={confirm} loading={busy} />
    </Screen>
  );
}
