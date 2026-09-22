import React, { useState } from 'react';
import { Text } from 'react-native';
import { useRouter } from 'expo-router';
import { api } from '../lib/api';
import { Button, ErrorText, Field, Notice, Screen, Title } from '../components/ui';
import { colors } from '../lib/theme';

export default function Forgot() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      setError('Enter the email address on your account.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setSent((await api.forgot(email.trim())).message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen keyboard>
      <Title>Reset your password</Title>
      {sent ? (
        <>
          <Notice tone="pine" title="Check your email">{sent}</Notice>
          <Text style={{ fontSize: 15, color: colors.ink2, marginBottom: 16 }}>
            Open the link on this phone, choose a new password, then come back and log in.
          </Text>
          <Button title="Back to log in" onPress={() => router.replace('/login')} />
        </>
      ) : (
        <>
          <Text style={{ fontSize: 16, color: colors.ink2, marginBottom: 20 }}>We’ll email you a link to choose a new one. It works for 2 hours.</Text>
          <ErrorText>{error}</ErrorText>
          <Field label="Email" value={email} onChangeText={setEmail} autoCapitalize="none" autoCorrect={false}
            keyboardType="email-address" textContentType="emailAddress" returnKeyType="send" onSubmitEditing={submit} />
          <Button title="Email me a reset link" onPress={submit} loading={busy} />
        </>
      )}
    </Screen>
  );
}
