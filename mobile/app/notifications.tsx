import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { api, Notice as NoticeT } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useLoad } from '../lib/useLoad';
import { appRouteFor } from '../lib/links';
import { ago } from '../lib/format';
import { Button, Empty, ErrorText, Loading, Screen } from '../components/ui';
import { colors, radius } from '../lib/theme';

export default function Notifications() {
  const router = useRouter();
  const { user, refresh: refreshCounts } = useAuth();
  const { data, error, refreshing, refresh, setData } = useLoad(() => api.notifications());

  async function open(n: NoticeT) {
    if (!n.read) {
      api.readNotification(n.id).then(refreshCounts).catch(() => {});
      setData((d) => d && { ...d, notifications: d.notifications.map((x) => (x.id === n.id ? { ...x, read: true } : x)) });
    }
    const route = appRouteFor(n.link, user?.role);
    if (route !== '/notifications') router.push(route as any);
  }

  async function readAll() {
    await api.readAllNotifications().catch(() => {});
    refreshCounts();
    refresh();
  }

  if (!data && !error) return <Loading />;
  const items = data?.notifications || [];
  const unread = items.filter((n) => !n.read).length;

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      {unread ? <Button title={`Mark all ${unread} as read`} variant="quiet" small onPress={readAll} /> : null}
      {items.length === 0 && !error ? (
        <Empty icon="notifications-outline" title="Nothing yet" body="New jobs, quotes and messages will show up here." />
      ) : null}
      {items.map((n) => (
        <Pressable key={n.id} onPress={() => open(n)} accessibilityRole="button"
          style={({ pressed }) => ({
            backgroundColor: pressed ? colors.slab : colors.paper, borderRadius: radius, padding: 14, marginBottom: 8,
            borderLeftWidth: 4, borderLeftColor: n.read ? colors.rule : colors.chalk, flexDirection: 'row', gap: 10,
          })}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 15.5, lineHeight: 21, color: colors.ink, fontWeight: n.read ? '400' : '600' }}>{n.body}</Text>
            <Text style={{ fontSize: 13, color: colors.ink3, marginTop: 4 }}>{ago(n.created_at)}</Text>
          </View>
        </Pressable>
      ))}
    </Screen>
  );
}
