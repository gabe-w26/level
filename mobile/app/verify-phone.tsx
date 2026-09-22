import React, { useEffect, useState } from 'react';
import { Alert, Text } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { Button, ErrorText, Field, Notice, Screen, Title } from '../components/ui';
import { colors } from '../lib/theme';

/** Customers confirm their mobile with a texted code before their first job goes out. */
export default function VerifyPhone() {
  const { job } = useLocalSearchParams<{ job?: string }>();
  const router = useRouter();
  const { user, refresh } = useAuth();
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Posting a held job already texted a code; this is only for opening the screen later.
    if (!job) resend(true);
  }, []);                                  // eslint-disable-line react-hooks/exhaustive-deps

  async function resend(quiet = false) {
    try {
      const res = await api.sendPhoneCode();
      if (res.verified) {
        refresh();
        router.back();
      } else if (!quiet) {
        setInfo(res.sent ? 'New code sent.' : 'Couldn’t send a text just now — try again shortly.');
      }
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.verifyPhone(code.trim());
      await refresh();
      Alert.alert('Phone confirmed', res.released_to
        ? `Your job has gone out to ${res.released_to} local trades.`
        : 'Your job is live — we’ll offer it as matching trades join.');
      if (job) router.replace(`/job/${job}`);
      else router.back();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen keyboard>
      <Title>Check your texts</Title>
      <Text style={{ fontSize: 16, color: colors.ink2, marginBottom: 20 }}>
        We sent a 6-digit code to {user?.phone || 'your mobile'}. Enter it here and your job goes straight out to local trades.
        It keeps fake jobs away from tradies.
      </Text>
      {info ? <Notice tone="chalk">{info}</Notice> : null}
      <ErrorText>{error}</ErrorText>
      <Field label="Code" value={code} onChangeText={setCode} keyboardType="number-pad" maxLength={6}
        textContentType="oneTimeCode" autoComplete="sms-otp" style={{ fontSize: 24, letterSpacing: 6 }} />
      <Button title="Confirm" onPress={submit} loading={busy} disabled={code.trim().length < 6} />
      <Button title="Send me a new code" variant="ghost" onPress={() => resend(false)} />
    </Screen>
  );
}
