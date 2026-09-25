import React, { useState } from 'react';
import { Alert, Image, Linking, ScrollView, Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { api, Quote } from '../../lib/api';
import { useLoad } from '../../lib/useLoad';
import { ago, day, JOB_STATUS, QUOTE_STATUS } from '../../lib/format';
import { Body, Button, Card, Empty, ErrorText, Label, Loading, Notice, Pill, QuoteMeter, Row, Screen, Title } from '../../components/ui';
import { colors, radius, space } from '../../lib/theme';
import { PromiseLine, ProgressSection } from '../../components/Progress';
import { SiteSection } from '../../components/Site';

export default function CustomerJob() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const jobId = Number(id);
  const router = useRouter();
  const { data, error, refreshing, refresh, reload } = useLoad(() => api.customerJob(jobId), 60000);
  const [busy, setBusy] = useState<string | null>(null);

  if (!data && !error) return <Loading />;
  if (!data) return <Screen><ErrorText>{error}</ErrorText></Screen>;

  const { job, quotes, stats, photos } = data;
  const status = JOB_STATUS[job.status] || { label: job.status, tone: 'muted' as const };
  const open = job.status === 'open' || job.status === 'full';

  async function act(key: string, fn: () => Promise<{ message?: string }>) {
    setBusy(key);
    try {
      const res = await fn();
      if (res.message) Alert.alert(res.message);
      await reload();
    } catch (e: any) {
      Alert.alert('That didn’t go through', e.message);
    } finally {
      setBusy(null);
    }
  }

  function share(q: Quote) {
    Alert.alert(`Share your details with ${q.business_name}?`,
      'They’ll see your name, phone number, email and address so they can get in touch.', [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Share', onPress: () => act(`share${q.id}`, () => api.quoteAction(jobId, q.id, 'share')) },
      ]);
  }

  function accept(q: Quote) {
    const go = (ack: boolean) => act(`accept${q.id}`, () => api.quoteAction(jobId, q.id, 'accept', ack));
    Alert.alert(`Accept ${q.business_name}’s quote?`, 'The other trades will be told you’ve gone with someone else.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Accept',
        onPress: () => {
          if (!q.needs_act) return go(false);
          // Building Act: residential work this size needs the paperwork before it starts
          Alert.alert('One more thing',
            'For residential work this size, the law requires a written contract, a disclosure statement and MBIE’s checklist before work starts. Will you get these from the trade?', [
              { text: 'Cancel', style: 'cancel' },
              { text: 'Yes, I will', onPress: () => go(true) },
            ]);
        },
      },
    ]);
  }

  function decline(q: Quote) {
    Alert.alert('Decline this quote?', `${q.business_name} will be told.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Decline', style: 'destructive', onPress: () => act(`decline${q.id}`, () => api.quoteAction(jobId, q.id, 'decline')) },
    ]);
  }

  function close() {
    Alert.alert('Close this job?', 'Trades still waiting on it will be told. What happened?', [
      { text: 'I hired someone else', onPress: () => act('close', () => api.closeJob(jobId, 'elsewhere')) },
      { text: 'I’m not going ahead', onPress: () => act('close', () => api.closeJob(jobId, 'not_going_ahead')) },
      { text: 'Cancel', style: 'cancel' },
    ]);
  }

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      <Pill label={status.label} tone={status.tone} />
      <View style={{ height: 8 }} />
      <Title size={24}>{job.title}</Title>
      <Text style={{ fontSize: 15, color: colors.ink2, marginBottom: space.md }}>
        {job.category_name} · {job.suburb}, {job.area_name} · posted {day(job.created_at)}
      </Text>

      {data.held ? (
        <Notice tone="paint" title="Confirm your phone to send this job out">
          <Text style={{ fontSize: 15, marginBottom: 10 }}>It’s a quick text code — it keeps fake jobs away from tradies.</Text>
          <Button title="Confirm my phone" small onPress={() => router.push({ pathname: '/verify-phone', params: { job: String(jobId) } })} />
        </Notice>
      ) : null}

      {open ? (
        <Card>
          <QuoteMeter count={job.quote_count} max={job.max_quotes} />
          <Body muted style={{ marginTop: 8, fontSize: 15 }}>
            {stats.offered
              ? `Offered to ${stats.offered} trade${stats.offered === 1 ? '' : 's'} so far. ${stats.waiting} deciding now, ${stats.quoted} quoted.`
              : 'No trades cover this area yet — we’ll offer it the moment one joins.'}
          </Body>
        </Card>
      ) : null}

      {/* Once someone's hired, their updates matter more than the quotes, so they come first. */}
      {data.progress ? <ProgressSection progress={data.progress} role="customer" jobId={jobId} onChanged={reload} /> : null}

      <Label style={{ marginTop: space.md, marginBottom: 8 }}>Quotes ({quotes.length})</Label>
      {quotes.length > 1 && open ? <BeforeYouChoose big={job.value_band !== 'small'} /> : null}
      {quotes.length === 0 ? (
        <Card><Empty icon="hourglass-outline" title="No quotes yet" body="We’ll let you know the moment one arrives." /></Card>
      ) : quotes.map((q, i) => (
        <QuoteCard key={q.id} q={q} n={i + 1} jobOpen={open} busy={busy}
          onShare={() => share(q)} onAccept={() => accept(q)} onDecline={() => decline(q)}
          onMessage={() => router.push(`/thread/${jobId}/${q.trade_id}`)} />
      ))}

      {data.site ? (
        <SiteSection site={data.site} jobId={jobId} role="customer" onChanged={reload} />
      ) : null}

      {data.can_review ? (
        <Button title="Leave a review" icon="star-outline" variant="pine" onPress={() => router.push(`/review/${jobId}`)} />
      ) : data.reviewed ? <Notice tone="pine">Thanks — you’ve reviewed this job.</Notice> : null}

      <Label style={{ marginTop: space.lg, marginBottom: 8 }}>Your job</Label>
      <Card>
        <Body selectable>{job.description}</Body>
        <View style={{ height: 10 }} />
        <Row label="Budget" value={job.value_label || ''} />
        <Row label="Timing" value={job.timing_label || ''} />
        <Row label="Property" value={job.property_label || ''} />
        <Row label="Address" value={job.address || ''} />
        <Row label="Closes" value={open ? day(job.closes_at) : ''} />
        {photos.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 10 }}>
            {photos.map((p, i) => (
              <Image key={p} source={{ uri: p }} accessibilityLabel={`Job photo ${i + 1}`}
                style={{ width: 120, height: 120, borderRadius: radius, marginRight: 8, backgroundColor: colors.ruleSoft }} />
            ))}
          </ScrollView>
        ) : null}
      </Card>

      {data.can_close ? (
        <Button title="Close this job" variant="quiet" loading={busy === 'close'} onPress={close} style={{ marginTop: space.sm }} />
      ) : null}
    </Screen>
  );
}

function BeforeYouChoose({ big }: { big: boolean }) {
  const [open, setOpen] = useState(true);
  const points = [
    ['Compare what’s included, not just the price.', 'Two quotes for the same job can cover different work — any differences are flagged under each price.'],
    ['Check the GST.', 'A price “plus GST” is 15% more than it looks beside one that includes it.'],
    ['Licences matter.', 'Electrical, gas and sanitary plumbing must be done by a registered person. We show what we’ve checked, and when.'],
    ['Insurance is yours to lose.', 'If something gets damaged and the trade isn’t insured, you carry it.'],
    ['Go careful on big deposits.', 'A deposit for materials is normal; most of the money up front is how people get burned.'],
    ...(big ? [['Over $30,000, the law is on your side.', 'Residential work that size needs a written contract, a disclosure statement and a checklist before work starts.']] : []),
  ];
  return (
    <Card>
      <Text
        onPress={() => setOpen(!open)}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        // A bare Text is not a 44pt target. Padding gives it the hit region a
        // finger needs, pulled back with a negative margin so nothing moves.
        style={{ fontWeight: '800', color: colors.ink, fontSize: 16,
                 paddingVertical: 12, marginVertical: -12 }}>
        <Ionicons name={open ? 'chevron-down' : 'chevron-forward'} size={15} color={colors.ink3} /> Before you choose
      </Text>
      {open ? points.map(([head, body]) => (
        <View key={head} style={{ flexDirection: 'row', gap: 8, marginTop: 10 }}>
          <Ionicons name="checkmark-circle-outline" size={16} color={colors.pine} style={{ marginTop: 2 }} />
          <Text style={{ flex: 1, fontSize: 14, color: colors.ink2 }}>
            <Text style={{ fontWeight: '700', color: colors.ink }}>{head}</Text> {body}
          </Text>
        </View>
      )) : null}
    </Card>
  );
}

function QuoteCard({ q, n, jobOpen, busy, onShare, onAccept, onDecline, onMessage }: {
  q: Quote; n: number; jobOpen: boolean; busy: string | null;
  onShare: () => void; onAccept: () => void; onDecline: () => void; onMessage: () => void;
}) {
  const status = QUOTE_STATUS[q.status];
  const badges = [q.licence_checked && 'Licence checked', q.insurance_checked && 'Insured', q.nzbn_checked && 'NZBN checked']
    .filter(Boolean) as string[];
  const canRespond = jobOpen && (q.status === 'sent' || q.status === 'shortlisted');
  return (
    <Card>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 12, color: colors.ink3, fontWeight: '700' }}>QUOTE {n} · {ago(q.created_at).toUpperCase()}</Text>
          <Text style={{ fontSize: 18, fontWeight: '800', color: colors.ink, marginTop: 2 }}>{q.business_name}</Text>
          {q.rating?.n ? (
            <Text style={{ fontSize: 14, color: colors.ink2 }}>
              <Ionicons name="star" size={13} color={colors.paint} /> {(q.rating.avg || 0).toFixed(1)} · {q.rating.n} review{q.rating.n === 1 ? '' : 's'}
            </Text>
          ) : <Text style={{ fontSize: 14, color: colors.ink3 }}>No reviews yet</Text>}
        </View>
      </View>
      {badges.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {badges.map((b) => <Pill key={b} label={b} tone="pine" />)}
        </View>
      ) : null}
      {q.trust ? (
        <View style={{ marginTop: 10, padding: 10, backgroundColor: colors.slab, borderRadius: radius }}>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 6 }}>
            <Text style={{ fontWeight: '800', fontSize: 16, color: colors.ink }}>{q.trust.score}</Text>
            <Text style={{ fontSize: 12, color: colors.ink3 }}>/100 · {q.trust.band}</Text>
          </View>
          {q.trust.lines.map((line, i) => (
            <Text key={i} style={{ fontSize: 13, color: colors.ink2, marginTop: 2 }}>{line}</Text>
          ))}
        </View>
      ) : null}
      <PromiseLine text={q.report_plan_text} record={q.report_record} />
      <Body style={{ marginTop: 10 }} selectable>{q.message}</Body>
      <View style={{ height: 6 }} />
      <Row label="Includes" value={q.inclusions || ''} />
      <Row label="Doesn’t include" value={q.exclusions || ''} />
      <Row label="Warranty" value={q.warranty || ''} />
      <Row label="Can start" value={q.available_from || ''} />
      <Row label="Takes about" value={q.duration || ''} />
      {q.items?.length ? (
        <View style={{ marginTop: 10 }}>
          <Text style={{ fontSize: 12, color: colors.ink3, fontWeight: '700' }}>WHERE THE MONEY GOES</Text>
          {q.items.map((it, i) => (
            <View key={i} style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
              <Text style={{ flex: 1, fontSize: 14, color: colors.ink2 }}>
                {it.description}
                {it.qty ? <Text style={{ color: colors.ink3 }}>  {it.qty}{it.unit ? ` ${it.unit}` : ''}</Text> : null}
              </Text>
              {it.total != null ? (
                <Text style={{ fontSize: 14, color: colors.ink, fontVariant: ['tabular-nums'] }}>
                  ${it.total.toFixed(2)}
                </Text>
              ) : null}
            </View>
          ))}
        </View>
      ) : null}
      {/* The price sits with what you get for it, not in the heading — the cheapest quote is
          usually the smallest one, and putting it on top invites people to read it alone. */}
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8, marginTop: 10, paddingTop: 10,
                     borderTopWidth: 1, borderTopColor: colors.ruleSoft }}>
        <Text style={{ fontSize: 12, color: colors.ink3, fontWeight: '700' }}>PRICE</Text>
        <Text style={{ fontSize: 20, fontWeight: '800', color: colors.ink }}>{q.price_text}</Text>
        {q.price_type !== 'site_visit' ? (
          <Text style={{ fontSize: 13, color: colors.ink2 }}>{q.gst_included ? 'incl. GST' : 'plus GST'}</Text>
        ) : null}
      </View>
      {q.notes?.length ? (
        <View style={{ marginTop: 8 }}>
          {q.notes.map((note, i) => (
            <View key={i} style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
              <Text style={{ color: colors.ink3 }}>•</Text>
              <Text style={{ flex: 1, fontSize: 14, color: colors.ink2 }}>{note.text}</Text>
            </View>
          ))}
          <Text style={{ fontSize: 13, color: colors.ink3, marginTop: 6 }}>
            Worth asking about before you choose — a cheaper price often means a smaller job, not a cheaper one.
          </Text>
        </View>
      ) : null}
      {q.needs_act ? (
        <Notice tone="chalk">This quote is $30,000 or more. For residential work this size the law requires a written contract, disclosure statement and MBIE checklist — the trade has confirmed they’ll provide them.</Notice>
      ) : null}
      {q.contact ? (
        <View style={{ backgroundColor: colors.slab, borderRadius: radius, padding: 12, marginTop: 8 }}>
          <Text style={{ fontWeight: '700', marginBottom: 6, color: colors.ink }}>{q.contact.name}</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {q.contact.phone ? <Button title="Call" icon="call-outline" small variant="quiet" style={{ flex: 1 }}
              onPress={() => Linking.openURL(`tel:${q.contact!.phone!.replace(/[^\d+]/g, '')}`)} /> : null}
            <Button title="Email" icon="mail-outline" small variant="quiet" style={{ flex: 1 }}
              onPress={() => Linking.openURL(`mailto:${q.contact!.email}`)} />
          </View>
        </View>
      ) : null}

      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12, marginBottom: 8 }}>
        <Pill label={status?.label || q.status} tone={status?.tone || 'muted'} />
        {q.msg_count ? <Text style={{ fontSize: 13, color: colors.ink2 }}>{q.msg_count} message{q.msg_count === 1 ? '' : 's'}</Text> : null}
      </View>
      <Button title="Message" icon="chatbubble-outline" variant="quiet" small onPress={onMessage} />
      {canRespond ? (
        <>
          {q.status === 'sent' ? (
            <Button title="Share my contact details" icon="person-add-outline" variant="quiet" small loading={busy === `share${q.id}`} onPress={onShare} />
          ) : null}
          <Button title="Accept quote" icon="checkmark-circle-outline" variant="pine" small loading={busy === `accept${q.id}`} onPress={onAccept} />
          <Button title="Decline" variant="ghost" small loading={busy === `decline${q.id}`} onPress={onDecline} />
        </>
      ) : null}
    </Card>
  );
}
