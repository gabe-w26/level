import React from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { api } from '../../lib/api';
import { useLoad } from '../../lib/useLoad';
import { ago, QUOTE_STATUS } from '../../lib/format';
import { Card, Empty, ErrorText, Loading, Pill, Screen } from '../../components/ui';
import { colors, space } from '../../lib/theme';

export default function MyQuotes() {
  const router = useRouter();
  const { data, error, refreshing, refresh } = useLoad(() => api.myQuotes());

  if (!data && !error) return <Loading />;
  const quotes = data?.quotes || [];
  const won = quotes.filter((q) => q.status === 'accepted').length;
  const live = quotes.filter((q) => q.status === 'sent' || q.status === 'shortlisted').length;

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      {quotes.length ? (
        <View style={{ flexDirection: 'row', gap: 10, marginBottom: space.md }}>
          <Stat n={quotes.length} label="Sent" />
          <Stat n={live} label="Waiting" />
          <Stat n={won} label="Won" />
        </View>
      ) : !error ? (
        <Empty icon="document-text-outline" title="No quotes yet" body="Quotes you send from the Jobs tab show up here." />
      ) : null}
      {quotes.map((q) => {
        const status = QUOTE_STATUS[q.status];
        return (
          <Card key={q.id} onPress={() => router.push(`/offer/${q.job_id}`)}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
              <Text style={{ flex: 1, fontSize: 17, fontWeight: '700', color: colors.ink }}>{q.title}</Text>
              <Text style={{ fontSize: 17, fontWeight: '800', color: colors.ink }}>{q.price_text}</Text>
            </View>
            <Text style={{ fontSize: 14.5, color: colors.ink2, marginTop: 3, marginBottom: 10 }}>
              {q.category_name} · {q.suburb}, {q.area_name} · sent {ago(q.created_at)}
            </Text>
            <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
              <Pill label={status?.label || q.status} tone={status?.tone || 'muted'} />
              {q.unread ? <Pill label={`${q.unread} new message${q.unread === 1 ? '' : 's'}`} tone="paint" /> : null}
            </View>
          </Card>
        );
      })}
    </Screen>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <View style={{ flex: 1, backgroundColor: colors.paper, borderRadius: 10, padding: 12, borderWidth: 1, borderColor: colors.rule }}>
      <Text style={{ fontSize: 24, fontWeight: '800', color: colors.ink, fontVariant: ['tabular-nums'] }}>{n}</Text>
      <Text style={{ fontSize: 13, color: colors.ink2 }}>{label}</Text>
    </View>
  );
}
