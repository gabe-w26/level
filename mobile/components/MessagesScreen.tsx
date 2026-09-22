import React from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useLoad } from '../lib/useLoad';
import { ago } from '../lib/format';
import { Card, Empty, ErrorText, Loading, Screen } from './ui';
import { colors } from '../lib/theme';

/** Every conversation, newest first. A thread exists for each quote on a job. */
export function MessagesScreen() {
  const router = useRouter();
  const { user, refresh: refreshCounts } = useAuth();
  const { data, error, refreshing, refresh } = useLoad(async () => {
    refreshCounts();
    return api.threads();
  }, 30000);

  if (!data && !error) return <Loading />;
  const threads = data?.threads || [];

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      {threads.length === 0 && !error ? (
        <Empty icon="chatbubbles-outline" title="No conversations yet"
          body={user?.role === 'trade'
            ? 'Once you quote on a job you can message the customer here.'
            : 'When a trade quotes on your job you can message them here.'} />
      ) : null}
      {threads.map((t) => (
        <Card key={`${t.job_id}-${t.trade_id}`} onPress={() => router.push(`/thread/${t.job_id}/${t.trade_id}`)}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ flex: 1, fontSize: 17, fontWeight: '700', color: colors.ink }} numberOfLines={1}>{t.with}</Text>
            <Text style={{ fontSize: 13, color: colors.ink3 }}>{ago(t.last_at)}</Text>
          </View>
          <Text style={{ fontSize: 14, color: colors.ink2, marginTop: 2 }} numberOfLines={1}>{t.title}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 6, gap: 8 }}>
            <Text style={{ flex: 1, fontSize: 15, color: t.unread ? colors.ink : colors.ink3, fontWeight: t.unread ? '600' : '400' }}
              numberOfLines={2}>
              {t.last_body || 'No messages yet — say kia ora.'}
            </Text>
            {t.unread ? (
              <View style={{ backgroundColor: colors.paint, borderRadius: 10, minWidth: 22, height: 22, alignItems: 'center',
                justifyContent: 'center', paddingHorizontal: 6 }}>
                <Text style={{ color: '#fff', fontWeight: '800', fontSize: 12 }}>{t.unread}</Text>
              </View>
            ) : null}
          </View>
        </Card>
      ))}
    </Screen>
  );
}
