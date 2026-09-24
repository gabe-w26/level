import React, { useMemo, useState } from 'react';
import { Share, Text, View } from 'react-native';
import { api, RecommendInput } from '../lib/api';
import { useAppConfig } from '../lib/auth';
import { useLoad } from '../lib/useLoad';
import { Body, Button, Card, Check, ErrorText, Field, Label, Loading, Notice, Pill, Screen, Title } from '../components/ui';
import { PickerField } from '../components/PickerField';
import { colors, space } from '../lib/theme';

const BLANK: RecommendInput = { name: '', business_name: '', email: '', phone: '', note: '', category_id: '', area_id: '', email_them: true };

/** Customers share Level with friends, and recommend tradies they rate. */
export default function ShareAndRecommend() {
  const config = useAppConfig();
  const { data, error, refreshing, refresh, reload } = useLoad(() => api.customerShare());
  const [f, setF] = useState<RecommendInput>(BLANK);
  const [formError, setFormError] = useState<string | null>(null);
  const [done, setDone] = useState<{ message: string; link?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof RecommendInput>(k: K) => (v: RecommendInput[K]) => setF((old) => ({ ...old, [k]: v }));

  const categories = useMemo(() => [{ data: (config?.categories || []).map((c) => ({ key: String(c.id), label: c.name })) }], [config]);
  const areas = useMemo(() => (config?.regions || []).map((r) => ({
    title: r.region, data: r.areas.map((a) => ({ key: String(a.id), label: a.name })),
  })), [config]);

  if ((!data && !error) || !config) return <Loading />;

  async function shareLink() {
    if (!data) return;
    try {
      await Share.share({
        message: `I found my tradie on Level — post a job free and get quotes from local trades: ${data.share_url}`,
        url: data.share_url,
      });
    } catch (_) {}
  }

  async function shareJoin(link: string) {
    try {
      await Share.share({ message: `I recommended you on Level, where homeowners post jobs for local trades. Join here: ${link}`, url: link });
    } catch (_) {}
  }

  async function submit() {
    setBusy(true);
    setFormError(null);
    try {
      const res = await api.recommend({ ...f, email_them: !!f.email && f.email_them });
      setDone({ message: res.message || 'Thanks!', link: res.emailed ? undefined : res.join_url });
      setF(BLANK);
      reload();
    } catch (e: any) {
      setFormError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen keyboard refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      <Title>Share Level</Title>
      <Body muted style={{ marginBottom: space.md }}>Know someone with a job coming up? Posting is free.</Body>
      {data ? (
        <Card>
          <Label style={{ marginBottom: 6 }}>Your link</Label>
          <Text selectable style={{ fontSize: 16, color: colors.chalkDeep, marginBottom: 12 }}>{data.share_url}</Text>
          <Button title="Share with a friend" icon="share-outline" onPress={shareLink} />
          {data.friends ? <Text style={{ fontSize: 14, color: colors.ink2 }}>{data.friends} friend{data.friends === 1 ? ' has' : 's have'} joined — thank you.</Text> : null}
        </Card>
      ) : null}

      <Title size={22}>Recommend a tradie</Title>
      <Body muted style={{ marginBottom: space.md }}>
        Had great work done by someone who isn’t on Level? Tell us who they are and we’ll invite them.
      </Body>
      {done ? (
        <Notice tone="pine" title="Recommendation saved">
          <Text style={{ fontSize: 15, marginBottom: done.link ? 10 : 0 }}>{done.message}</Text>
          {done.link ? <Button title="Send them the link" small icon="share-outline" onPress={() => shareJoin(done.link!)} /> : null}
        </Notice>
      ) : null}
      <ErrorText>{formError}</ErrorText>
      <Field label="Their name" value={f.name} onChangeText={set('name')} textContentType="name" />
      <Field label="Business name (if you know it)" value={f.business_name} onChangeText={set('business_name')} />
      <PickerField label="Their trade" placeholder="Choose a trade" sections={categories} value={String(f.category_id)}
        onChange={(v) => set('category_id')(Number(v))} />
      <PickerField label="Area they work in" placeholder="Choose an area" sections={areas} value={String(f.area_id)}
        onChange={(v) => set('area_id')(Number(v))} />
      <Field label="Their email" value={f.email} onChangeText={set('email')} autoCapitalize="none" autoCorrect={false}
        keyboardType="email-address" hint="Email or mobile — at least one." />
      <Field label="Their mobile" value={f.phone} onChangeText={set('phone')} keyboardType="phone-pad" />
      <Field label="Anything to add? (optional)" value={f.note} onChangeText={set('note')} multiline style={{ minHeight: 80 }} />
      {f.email ? (
        <Check label="Email them an invite from me (just one email)" value={!!f.email_them} onChange={set('email_them')} />
      ) : null}
      <Button title="Recommend" icon="thumbs-up-outline" loading={busy} onPress={submit} />

      {data?.recommended.length ? (
        <>
          <Label style={{ marginTop: space.lg, marginBottom: 8 }}>Your recommendations</Label>
          {data.recommended.map((r, i) => (
            <Card key={`${r.business_name}-${i}`}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 16, fontWeight: '700', color: colors.ink }}>{r.business_name}</Text>
                  <Text style={{ fontSize: 13.5, color: colors.ink2 }}>{r.category_name}</Text>
                </View>
                <Pill label={r.joined ? 'Joined' : 'Invited'} tone={r.joined ? 'pine' : 'muted'} />
              </View>
            </Card>
          ))}
        </>
      ) : null}
    </Screen>
  );
}
