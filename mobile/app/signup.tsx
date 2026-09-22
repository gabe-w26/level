import React, { useState } from 'react';
import { Linking, Text } from 'react-native';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import * as Device from 'expo-device';
import { api, Role } from '../lib/api';
import { useAuth } from '../lib/auth';
import { SITE_URL } from '../lib/config';
import { Button, Choice, ErrorText, Field, Screen, Title } from '../components/ui';
import { colors } from '../lib/theme';

export default function Signup() {
  const params = useLocalSearchParams<{ role?: string }>();
  const router = useRouter();
  const navigation = useNavigation();
  const { signIn, config } = useAuth();
  const [role, setRole] = useState<Role>(params.role === 'trade' ? 'trade' : 'customer');
  const [f, setF] = useState({ name: '', email: '', phone: '', password: '', business_name: '' });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (v: string) => setF((old) => ({ ...old, [k]: v }));

  React.useEffect(() => {
    navigation.setOptions({ title: role === 'trade' ? 'Join as a tradie' : 'Sign up' });
  }, [role, navigation]);

  async function submit() {
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      const res = await api.signup({ role, ...f, business_name: role === 'trade' ? f.business_name : undefined,
        device: Device.modelName ?? undefined });
      await signIn(res.token, res.user);
    } catch (e: any) {
      setErrors(e.errors || {});
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const links = config?.links || { terms: `${SITE_URL}/terms`, privacy: `${SITE_URL}/privacy` };

  return (
    <Screen keyboard>
      <Title>{role === 'trade' ? 'Get local jobs, fairly' : 'Find a tradie you can trust'}</Title>
      <Text style={{ fontSize: 16, color: colors.ink2, marginBottom: 20 }}>
        {role === 'trade'
          ? 'Create your account here. You’ll finish your business profile — the trades you do and the areas you cover — on the Level website.'
          : 'Create a free account, then post your job. Trades see your suburb, never your address, until you choose them.'}
      </Text>
      <Choice<Role> options={[{ key: 'customer', label: 'I need a tradie' }, { key: 'trade', label: 'I’m a tradie' }]}
        value={role} onChange={setRole} />
      <ErrorText>{Object.keys(errors).length ? 'Check the highlighted fields.' : error}</ErrorText>
      <Field label="Your name" value={f.name} onChangeText={set('name')} error={errors.name} textContentType="name" autoComplete="name" />
      {role === 'trade' ? (
        <Field label="Business or trading name" value={f.business_name} onChangeText={set('business_name')} error={errors.business_name}
          textContentType="organizationName" />
      ) : null}
      <Field label="Email" value={f.email} onChangeText={set('email')} error={errors.email} autoCapitalize="none" autoCorrect={false}
        keyboardType="email-address" textContentType="emailAddress" autoComplete="email" />
      <Field label="Mobile number" value={f.phone} onChangeText={set('phone')} error={errors.phone} keyboardType="phone-pad"
        textContentType="telephoneNumber" autoComplete="tel" placeholder="021 123 4567" />
      <Field label="Password" value={f.password} onChangeText={set('password')} error={errors.password} secureTextEntry
        textContentType="newPassword" autoComplete="new-password" hint="At least 8 characters." />
      <Button title="Create account" onPress={submit} loading={busy} />
      <Text style={{ fontSize: 13.5, color: colors.ink2, lineHeight: 20, marginTop: 8 }}>
        By signing up you agree to Level’s{' '}
        <Text style={{ color: colors.chalkDeep, textDecorationLine: 'underline' }} onPress={() => Linking.openURL(links.terms)}>Terms</Text>
        {' '}and{' '}
        <Text style={{ color: colors.chalkDeep, textDecorationLine: 'underline' }} onPress={() => Linking.openURL(links.privacy)}>Privacy Policy</Text>.
      </Text>
      <Button title="I already have an account" variant="ghost" onPress={() => router.replace('/login')} style={{ marginTop: 16 }} />
    </Screen>
  );
}
