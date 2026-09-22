import React, { useState } from 'react';
import { Text } from 'react-native';
import { useRouter } from 'expo-router';
import * as Device from 'expo-device';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { Button, ErrorText, Field, Screen, Title } from '../components/ui';
import { colors } from '../lib/theme';

export default function Login() {
  const router = useRouter();
  const { signIn } = useAuth();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!login.trim() || !password) {
      setError('Enter your email or username, and your password.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await api.login(login.trim(), password, Device.modelName ?? undefined);
      await signIn(res.token, res.user);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen keyboard>
      <Title>Welcome back</Title>
      <Text style={{ fontSize: 16, color: colors.ink2, marginBottom: 20 }}>Log in with the email or username you use on the Level website.</Text>
      <ErrorText>{error}</ErrorText>
      <Field label="Email or username" value={login} onChangeText={setLogin} autoCapitalize="none" autoCorrect={false}
        keyboardType="email-address" textContentType="username" autoComplete="username" returnKeyType="next" />
      <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry textContentType="password"
        autoComplete="password" returnKeyType="go" onSubmitEditing={submit} />
      <Button title="Log in" onPress={submit} loading={busy} />
      <Button title="Forgot your password?" variant="ghost" onPress={() => router.push('/forgot')} />
    </Screen>
  );
}
