import React, { useEffect, useState } from 'react';
import { Alert, Pressable, ScrollView, Text, View } from 'react-native';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { api, QuoteInput, TradeJobDetail } from '../../lib/api';
import { Button, Check, Choice, ErrorText, Field, Loading, Notice, Screen } from '../../components/ui';
import { colors, radius, space } from '../../lib/theme';

type PriceType = QuoteInput['price_type'];

/** Send (or edit) a quote. The server's engine does the real checking. */
export default function QuoteForm() {
  const { id, edit } = useLocalSearchParams<{ id: string; edit?: string }>();
  const jobId = Number(id);
  const editing = edit === '1';
  const router = useRouter();
  const navigation = useNavigation();
  const [detail, setDetail] = useState<TradeJobDetail | null>(null);
  const [f, setF] = useState<QuoteInput>({ price_type: 'fixed', gst: 'incl', message: '' });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof QuoteInput) => (v: any) => setF((old) => ({ ...old, [k]: v }));

  useEffect(() => {
    navigation.setOptions({ title: editing ? 'Edit your quote' : 'Send a quote' });
    api.tradeJob(jobId).then((d) => {
      setDetail(d);
      if (editing && d.quote) {
        const q = d.quote;
        setF({
          price_type: q.price_type, gst: q.gst_included ? 'incl' : 'excl', message: q.message,
          amount_low: q.amount_low ? String(q.amount_low) : '', amount_high: q.amount_high ? String(q.amount_high) : '',
          inclusions: q.inclusions || '', exclusions: q.exclusions || '', warranty: q.warranty || '',
          available_from: q.available_from || '', duration: q.duration || '', act_docs_promised: q.act_docs_promised,
        });
      }
    }).catch((e) => setError(e.message));
  }, [jobId, editing, navigation]);

  if (!detail && !error) return <Loading />;

  const top = Number((f.price_type === 'range' ? f.amount_high : f.amount_low)?.replace(/[^\d.]/g, '') || 0);
  const topInclGst = f.gst === 'incl' ? top : top * 1.15;
  const threshold = detail?.contract_threshold || 30000;
  const needsAct = f.price_type !== 'site_visit' && topInclGst >= threshold && detail?.job.property_type !== 'commercial';

  function applyTemplate(t: TradeJobDetail['templates'][number]) {
    setF((old) => ({
      ...old,
      price_type: (t.price_type as PriceType) || old.price_type,
      message: t.message || old.message, inclusions: t.inclusions || '', exclusions: t.exclusions || '',
      warranty: t.warranty || '', duration: t.duration || '',
    }));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = editing ? await api.editQuote(jobId, f) : await api.sendQuote(jobId, f);
      Alert.alert(editing ? 'Quote updated' : 'Quote sent', res.message);
      router.back();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen keyboard>
      {detail ? <Text style={{ fontSize: 17, fontWeight: '700', color: colors.ink, marginBottom: space.md }}>{detail.job.title}</Text> : null}
      <ErrorText>{error}</ErrorText>

      {!editing && detail?.templates.length ? (
        <View style={{ marginBottom: space.lg }}>
          <Text style={{ fontSize: 15, fontWeight: '600', color: colors.ink, marginBottom: 8 }}>Start from a template</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            {detail.templates.map((t) => (
              <Pressable key={t.id} onPress={() => applyTemplate(t)} accessibilityRole="button"
                style={{ paddingHorizontal: 14, minHeight: 44, justifyContent: 'center', borderRadius: radius, marginRight: 8,
                  backgroundColor: colors.chalkWash, borderWidth: 1, borderColor: colors.chalk }}>
                <Text style={{ color: colors.chalkDeep, fontWeight: '600' }}>{t.name}</Text>
              </Pressable>
            ))}
          </ScrollView>
        </View>
      ) : null}

      <Choice<PriceType> label="How do you want to price it?" value={f.price_type} onChange={set('price_type')}
        options={[{ key: 'fixed', label: 'Fixed price' }, { key: 'range', label: 'Price range' }, { key: 'site_visit', label: 'Site visit first' }]} />
      {f.price_type === 'fixed' ? (
        <Field label="Your price ($)" value={f.amount_low} onChangeText={set('amount_low')} keyboardType="numeric" placeholder="e.g. 1800" />
      ) : null}
      {f.price_type === 'range' ? (
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <View style={{ flex: 1 }}><Field label="From ($)" value={f.amount_low} onChangeText={set('amount_low')} keyboardType="numeric" /></View>
          <View style={{ flex: 1 }}><Field label="To ($)" value={f.amount_high} onChangeText={set('amount_high')} keyboardType="numeric" /></View>
        </View>
      ) : null}
      {f.price_type !== 'site_visit' ? (
        <Choice<'incl' | 'excl'> label="GST" value={f.gst} onChange={set('gst')}
          options={[{ key: 'incl', label: 'Includes GST' }, { key: 'excl', label: 'Plus GST' }]} />
      ) : null}
      <Field label="Message to the customer" value={f.message} onChangeText={set('message')} multiline
        placeholder="How you’d do the job, what you’d need to check, anything they should know."
        hint="At least a sentence or two. This is what the customer reads first." />
      <Field label="What’s included (optional)" value={f.inclusions} onChangeText={set('inclusions')} multiline style={{ minHeight: 80 }} />
      <Field label="What’s not included (optional)" value={f.exclusions} onChangeText={set('exclusions')} multiline style={{ minHeight: 80 }} />
      <Field label="Warranty (optional)" value={f.warranty} onChangeText={set('warranty')} placeholder="e.g. 2 years on workmanship" />
      <View style={{ flexDirection: 'row', gap: 10 }}>
        <View style={{ flex: 1 }}><Field label="Can start (optional)" value={f.available_from} onChangeText={set('available_from')} placeholder="e.g. next week" /></View>
        <View style={{ flex: 1 }}><Field label="Takes about (optional)" value={f.duration} onChangeText={set('duration')} placeholder="e.g. 2 days" /></View>
      </View>
      {needsAct ? (
        <Notice tone="chalk" title="$30,000 or more">
          <Text style={{ fontSize: 15, marginBottom: 6 }}>
            Residential work this size needs a written contract, a disclosure statement and MBIE’s checklist before work starts.
          </Text>
          <Check label="I’ll provide the contract, disclosure statement and checklist" value={!!f.act_docs_promised} onChange={set('act_docs_promised')} />
        </Notice>
      ) : null}
      <Button title={editing ? 'Save changes' : 'Send quote'} icon="send" onPress={submit} loading={busy} />
      <Text style={{ fontSize: 13.5, color: colors.ink2, textAlign: 'center' }}>
        {editing ? 'You can change your quote until the customer responds.' : 'Quotes arrive in order. The job closes at six.'}
      </Text>
    </Screen>
  );
}
