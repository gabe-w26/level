import React, { useMemo, useState } from 'react';
import { Alert, Image, Linking, ScrollView, Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { api } from '../../lib/api';
import { useLoad } from '../../lib/useLoad';
import { day, QUOTE_STATUS } from '../../lib/format';
import { Body, Button, Card, ErrorText, Label, Loading, Notice, Pill, QuoteMeter, Row, Screen, Title } from '../../components/ui';
import { Countdown } from '../../components/Countdown';
import { colors, radius, space } from '../../lib/theme';
import { ProgressSection } from '../../components/Progress';

export default function TradeJob() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const jobId = Number(id);
  const router = useRouter();
  const { data, error, refreshing, refresh, reload } = useLoad(async () => ({ ...(await api.tradeJob(jobId)), loadedAt: Date.now() }));
  const [busy, setBusy] = useState(false);
  const deadline = useMemo(() => (data ? data.loadedAt + data.offer.seconds_left * 1000 : 0), [data]);

  if (!data && !error) return <Loading />;
  if (!data) return <Screen><ErrorText>{error}</ErrorText></Screen>;

  const { job, offer, quote, contact, customer_record: rec, photos } = data;

  function pass() {
    Alert.alert('Pass on this job?', 'It goes straight to another trade, and you can’t get it back.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Pass', style: 'destructive', onPress: async () => {
          setBusy(true);
          try {
            await api.passJob(jobId);
            router.back();
          } catch (e: any) {
            Alert.alert('That didn’t go through', e.message);
            reload();
          } finally {
            setBusy(false);
          }
        },
      },
    ]);
  }

  const qStatus = quote ? QUOTE_STATUS[quote.status] : null;
  const lostSlot = !quote && !data.can_quote;

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      {data.can_quote ? <Countdown deadline={deadline} big /> : null}
      <View style={{ height: 10 }} />
      <Title size={24}>{job.title}</Title>
      <Text style={{ fontSize: 15, color: colors.ink2, marginBottom: space.md }}>
        {job.category_name} · {job.suburb}, {job.area_name}
      </Text>

      {quote ? (
        <Card>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <Label>Your quote</Label>
            <Pill label={qStatus?.label || quote.status} tone={qStatus?.tone || 'muted'} />
          </View>
          <Text style={{ fontSize: 22, fontWeight: '800', color: colors.ink, marginTop: 8 }}>
            {quote.price_text}{quote.price_type !== 'site_visit' ? <Text style={{ fontSize: 14, fontWeight: '400', color: colors.ink2 }}>  {quote.gst_included ? 'incl. GST' : 'plus GST'}</Text> : null}
          </Text>
          <Body style={{ marginTop: 8 }}>{quote.message}</Body>
          <Text style={{ fontSize: 14, color: colors.ink2, marginTop: 6 }}>
            Progress updates: {quote.report_plan_text || 'none promised'}
          </Text>
          <View style={{ height: 10 }} />
          {quote.status === 'shortlisted' ? <Notice tone="paint" title="The customer shared their details">Give them a call.</Notice> : null}
          {quote.status === 'accepted' ? <Notice tone="pine" title="You won this job">The customer’s details are below.</Notice> : null}
          {data.can_edit_quote && job.status !== 'closed' ? (
            <Button title="Edit my quote" variant="quiet" small onPress={() => router.push({ pathname: '/quote/[id]', params: { id: String(jobId), edit: '1' } })} />
          ) : null}
          <Button title="Message the customer" icon="chatbubble-outline" variant="quiet" small
            onPress={() => router.push(`/thread/${jobId}/${quote.trade_id}`)} />
        </Card>
      ) : null}

      {contact ? (
        <Card>
          <Label style={{ marginBottom: 6 }}>Customer</Label>
          <Text style={{ fontSize: 18, fontWeight: '700', color: colors.ink, marginBottom: 8 }}>{contact.name}</Text>
          <Row label="Address" value={job.address || ''} />
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
            {contact.phone ? <Button title="Call" icon="call-outline" small style={{ flex: 1 }}
              onPress={() => Linking.openURL(`tel:${contact.phone!.replace(/[^\d+]/g, '')}`)} /> : null}
            <Button title="Email" icon="mail-outline" small variant="quiet" style={{ flex: 1 }}
              onPress={() => Linking.openURL(`mailto:${contact.email}`)} />
          </View>
        </Card>
      ) : null}

      {data.progress ? <ProgressSection progress={data.progress} role="trade" jobId={jobId} onChanged={reload} /> : null}

      {lostSlot ? (
        <Notice tone="muted">
          {offer.status === 'declined' ? 'You passed on this job.'
            : offer.status === 'closed' ? 'This job filled up or closed before you quoted.'
            : 'Your time to quote ran out, so the job went to another trade.'}
        </Notice>
      ) : null}

      {data.can_quote ? (
        <>
          <Button title="Send a quote" icon="send" onPress={() => router.push({ pathname: '/quote/[id]', params: { id: String(jobId) } })} />
          <Button title="Pass on this job" variant="quiet" loading={busy} onPress={pass} />
        </>
      ) : null}

      <Card style={{ marginTop: space.sm }}>
        <QuoteMeter count={job.quote_count} max={job.max_quotes} />
        <View style={{ height: 10 }} />
        <Body selectable>{job.description}</Body>
        <View style={{ height: 10 }} />
        <Row label="Budget" value={job.value_label || ''} />
        <Row label="Timing" value={job.timing_label || ''} />
        <Row label="Property" value={job.property_label || ''} />
        <Row label="Where" value={`${job.suburb}, ${job.area_name}`} />
        {!contact ? <Text style={{ fontSize: 13, color: colors.ink3, marginTop: 4 }}>The street address is shared if the customer chooses you.</Text> : null}
        {photos.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 12 }}>
            {photos.map((p, i) => (
              <Image key={p} source={{ uri: p }} accessibilityLabel={`Job photo ${i + 1}`}
                style={{ width: 140, height: 140, borderRadius: radius, marginRight: 8, backgroundColor: colors.ruleSoft }} />
            ))}
          </ScrollView>
        ) : null}
        {job.licence_note ? <View style={{ marginTop: 12 }}><Notice tone="chalk">{job.licence_note}</Notice></View> : null}
      </Card>

      <Card>
        <Label style={{ marginBottom: 6 }}>About the customer</Label>
        <Row label="On Level since" value={rec.since || '—'} />
        <Row label="Jobs posted" value={String(rec.jobs)} />
        <Row label="Hired here" value={String(rec.hired)} />
        <Row label="Replied to quotes" value={rec.quotes ? `${rec.answered} of ${rec.quotes}` : 'No quotes yet'} />
        <Row label="Phone" value={rec.phone_verified ? 'Confirmed' : 'Not confirmed'} />
      </Card>
      <Text style={{ fontSize: 13, color: colors.ink3, textAlign: 'center' }}>Offered {day(offer.offered_at)}</Text>
    </Screen>
  );
}
