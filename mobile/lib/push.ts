import { useEffect } from 'react';
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import Constants from 'expo-constants';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useRouter } from 'expo-router';
import { api, Role } from './api';
import { appRouteFor } from './links';
import { colors } from './theme';

const STORED_TOKEN_KEY = 'level_push_token';
let lastOpened: string | null = null;           // so one tap is only ever handled once

// New jobs have a 24-hour clock, so show alerts even while the app is open.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

/**
 * Ask for permission, get an Expo push token and give it to the server.
 * Returns the token, or null where push can't work (Simulator, denied, or no
 * EAS project id yet).
 */
export async function registerForPush(): Promise<string | null> {
  if (!Device.isDevice) return null;           // simulators never get a push token

  const existing = await Notifications.getPermissionsAsync();
  let granted = existing.granted;
  if (!granted && existing.canAskAgain) {
    granted = (await Notifications.requestPermissionsAsync()).granted;
  }
  if (!granted) return null;

  if (Platform.OS === 'android') {
    // Android drops notifications that have no channel
    await Notifications.setNotificationChannelAsync('default', {
      name: 'Jobs, quotes and messages',
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: colors.chalk,
    });
  }

  try {
    const projectId = (Constants.expoConfig?.extra as any)?.eas?.projectId || Constants.easConfig?.projectId;
    const res = await Notifications.getExpoPushTokenAsync(projectId ? { projectId } : undefined);
    const token = res.data;
    if (!token) return null;
    await api.registerPushToken(token, Platform.OS);
    await AsyncStorage.setItem(STORED_TOKEN_KEY, token);
    return token;
  } catch (_) {
    return null;                                // no projectId configured yet, or offline
  }
}

/** Stop pushes to this phone (on sign out). Returns the token so logout can pass it too. */
export async function unregisterForPush(): Promise<string | null> {
  try {
    const token = await AsyncStorage.getItem(STORED_TOKEN_KEY);
    if (token) await api.unregisterPushToken(token).catch(() => {});
    await AsyncStorage.removeItem(STORED_TOKEN_KEY);
    return token;
  } catch (_) {
    return null;
  }
}

/** Registers once signed in, and sends taps to the screen the notification is about. */
export function usePushNotifications(role: Role | undefined, onReceived?: () => void) {
  const router = useRouter();

  useEffect(() => {
    if (!role) return;
    registerForPush();

    const open = (response: Notifications.NotificationResponse | null) => {
      if (!response || response.notification.request.identifier === lastOpened) return;
      lastOpened = response.notification.request.identifier;
      const link = response.notification.request.content.data?.link;
      if (typeof link === 'string') router.push(appRouteFor(link, role) as any);
    };
    // A tap that launched the app from closed
    Notifications.getLastNotificationResponseAsync().then(open).catch(() => {});
    const tapSub = Notifications.addNotificationResponseReceivedListener(open);
    const receiveSub = Notifications.addNotificationReceivedListener(() => onReceived?.());
    return () => {
      tapSub.remove();
      receiveSub.remove();
    };
  }, [role, router, onReceived]);
}
