import Constants from 'expo-constants';

/**
 * Where the app talks to. The one place to change it.
 *
 * Default: the live site. For local development either set
 *   EXPO_PUBLIC_API_URL=http://192.168.1.20:5077 npx expo start
 * (your Mac's Wi-Fi address, so a phone can reach it; the iOS Simulator can use
 * http://localhost:5077), or change `extra.apiUrl` in app.json.
 */
const fromEnv = process.env.EXPO_PUBLIC_API_URL;
const fromAppJson = (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl;

export const SITE_URL = (fromEnv || fromAppJson || 'https://level-wcyc.onrender.com').replace(/\/+$/, '');
export const API_URL = `${SITE_URL}/api/mobile`;
