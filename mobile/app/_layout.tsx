import { useEffect } from 'react';
import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { AuthProvider, useAuth } from '../lib/auth';
import { usePushNotifications } from '../lib/push';
import { colors } from '../lib/theme';

const PUBLIC = ['', 'index', 'login', 'signup', 'forgot'];

function RootGuard() {
  const { token, user, isLoading, refresh } = useAuth();
  const segments = useSegments();
  const router = useRouter();
  usePushNotifications(token && user ? user.role : undefined, refresh);

  useEffect(() => {
    if (isLoading) return;
    const first = (segments[0] as string | undefined) ?? '';
    const isPublic = PUBLIC.includes(first);
    const home = user?.role === 'trade' ? '/(trade)' : '/(customer)';
    if (!token && !isPublic) {
      router.replace('/');
    } else if (token && user && isPublic) {
      router.replace(home);
    } else if (user && ((first === '(trade)' && user.role !== 'trade') || (first === '(customer)' && user.role !== 'customer'))) {
      router.replace(home);
    }
  }, [token, user, isLoading, segments, router]);

  const header = {
    headerShown: true,
    headerBackTitle: 'Back',
    headerTintColor: colors.chalkDeep,
    headerStyle: { backgroundColor: colors.paper },
    headerTitleStyle: { color: colors.ink, fontWeight: '700' as const },
    contentStyle: { backgroundColor: colors.concrete },
  };

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.concrete } }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="login" options={{ ...header, title: 'Log in' }} />
      <Stack.Screen name="signup" options={{ ...header, title: 'Sign up' }} />
      <Stack.Screen name="forgot" options={{ ...header, title: 'Reset password' }} />
      <Stack.Screen name="(customer)" />
      <Stack.Screen name="(trade)" />
      <Stack.Screen name="job/[id]" options={{ ...header, title: 'Your job' }} />
      <Stack.Screen name="offer/[id]" options={{ ...header, title: 'Job' }} />
      <Stack.Screen name="quote/[id]" options={{ ...header, title: 'Send a quote', presentation: 'modal' }} />
      <Stack.Screen name="review/[id]" options={{ ...header, title: 'Leave a review', presentation: 'modal' }} />
      <Stack.Screen name="thread/[jobId]/[tradeId]" options={{ ...header, title: 'Messages' }} />
      <Stack.Screen name="notifications" options={{ ...header, title: 'Notifications' }} />
      <Stack.Screen name="verify-phone" options={{ ...header, title: 'Confirm your phone' }} />
      <Stack.Screen name="delete-account" options={{ ...header, title: 'Delete account' }} />
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <AuthProvider>
      <StatusBar style="dark" />
      <RootGuard />
    </AuthProvider>
  );
}
