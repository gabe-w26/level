import React, { useMemo } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { api } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { useLoad } from '../../lib/useLoad';
import { Card, Empty, ErrorText, Loading, Pill, QuoteMeter, Screen } from '../../components/ui';
import { SetupNotice } from '../../components/SetupNotice';
import { Countdown } from '../../components/Countdown';
import { colors, space } from '../../lib/theme';

export default function TradeJobs() {
  const router = useRouter();
  const { refresh: refreshCounts } = useAuth();
  const { data, error, refreshing, refresh } = useLoad(async () => {
    refreshCounts();
    const res = await api.offers();
    return { ...res, loadedAt: Date.now() };
  }, 60000);

  const offers = useMemo(() => (data?.offers || []).map((o) => ({ ...o, deadline: data!.loadedAt + o.seconds_left * 1000 })), [data]);

  if (!data && !error) return <Loading />;

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      {data?.trade ? <SetupNotice status={data.trade} /> : null}
      {data && offers.length === 0 ? (
        <Empty icon="flash-outline" title="No jobs waiting right now"
          body={data.trade?.paused
            ? 'New jobs are paused. Turn them back on from your Account tab.'
            : 'When a local job matches your trades and areas, it lands here and you’ll get a notification. You have 4 hours to quote.'} />
      ) : null}
      {offers.length ? (
        <Text style={{ fontSize: 15, color: colors.ink2, marginBottom: space.md }}>
          {offers.length} job{offers.length === 1 ? '' : 's'} waiting on you. Quote or pass before the time runs out — the slot goes to another trade.
        </Text>
      ) : null}
      {offers.map((o) => (
        <Card key={o.id} onPress={() => router.push(`/offer/${o.id}`)}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <Countdown deadline={o.deadline} />
            {!o.seen ? <Pill label="New" tone="paint" /> : null}
          </View>
          <Text style={{ fontSize: 18, fontWeight: '800', color: colors.ink }}>{o.title}</Text>
          <Text style={{ fontSize: 14.5, color: colors.ink2, marginTop: 3 }}>
            {o.category_name} · {o.suburb}, {o.area_name}
          </Text>
          <Text style={{ fontSize: 14.5, color: colors.ink2, marginTop: 2, marginBottom: 10 }}>
            {o.value_label}{o.timing_label ? ` · ${o.timing_label}` : ''}
          </Text>
          <Text style={{ fontSize: 15, color: colors.ink, lineHeight: 21, marginBottom: 10 }} numberOfLines={3}>{o.description}</Text>
          <QuoteMeter count={o.quote_count} max={o.max_quotes} />
        </Card>
      ))}
    </Screen>
  );
}
