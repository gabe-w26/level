import React from 'react';
import { Share, Text, View } from 'react-native';
import { api } from '../lib/api';
import { useLoad } from '../lib/useLoad';
import { day } from '../lib/format';
import { Body, Button, Card, Empty, ErrorText, Label, Loading, Pill, Screen, Title } from '../components/ui';
import { colors, space } from '../lib/theme';

/**
 * A trade's invite link. Rewards are described in months only — the app never
 * talks about prices or payment (App Store 3.1.1).
 */
export default function Referrals() {
  const { data, error, refreshing, refresh } = useLoad(() => api.tradeReferrals());

  if (!data && !error) return <Loading />;
  if (!data) return <Screen><ErrorText>{error}</ErrorText></Screen>;

  async function share() {
    try {
      await Share.share({
        message: `I get local jobs through Level — each job goes to a handful of trades and closes at six quotes. Join with my link: ${data!.invite_url}`,
        url: data!.invite_url,
      });
    } catch (_) {}
  }

  const m = data.months;
  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <Title>Invite a mate</Title>
      <Body muted style={{ marginBottom: space.lg }}>
        Know a good tradie? Send them your link. When they sign up and send their first quote, you get a free month.
      </Body>

      <Card>
        <Label style={{ marginBottom: 6 }}>Your invite link</Label>
        <Text selectable style={{ fontSize: 16, color: colors.chalkDeep, marginBottom: 12 }}>{data.invite_url}</Text>
        <Button title="Share my link" icon="share-outline" onPress={share} />
      </Card>

      <Card>
        <Label style={{ marginBottom: 8 }}>Free months</Label>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <Stat n={m.earned} label="Earned" />
          <Stat n={m.waiting} label="Banked" />
          <Stat n={m.used} label="Used" />
        </View>
        <Text style={{ fontSize: 13.5, color: colors.ink2, marginTop: 10, lineHeight: 19 }}>
          Banked months are used automatically. You can earn up to {m.cap}.
        </Text>
      </Card>

      <Label style={{ marginTop: space.md, marginBottom: 8 }}>Who joined with your link</Label>
      {data.joined.length === 0 ? (
        <Card><Empty icon="people-outline" title="Nobody yet" body="Share your link with a tradie you rate." /></Card>
      ) : data.joined.map((j, i) => (
        <Card key={`${j.business_name}-${i}`}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 16, fontWeight: '700', color: colors.ink }}>{j.business_name}</Text>
              <Text style={{ fontSize: 13.5, color: colors.ink2 }}>Joined {day(j.joined_at)}</Text>
            </View>
            <Pill label={j.quoted ? 'Sent a quote' : 'No quotes yet'} tone={j.quoted ? 'pine' : 'muted'} />
          </View>
        </Card>
      ))}
    </Screen>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <View style={{ flex: 1, backgroundColor: colors.slab, borderRadius: 10, padding: 12 }}>
      <Text style={{ fontSize: 26, fontWeight: '800', color: colors.ink, fontVariant: ['tabular-nums'] }}>{n}</Text>
      <Text style={{ fontSize: 13, color: colors.ink2 }}>{label}</Text>
    </View>
  );
}
