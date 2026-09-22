import React, { useEffect, useState } from 'react';
import { Alert, Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { api, PhotoInput, Progress, ReportKind, ReportRecord } from '../lib/api';
import { pickPhotos } from '../lib/photos';
import { ago } from '../lib/format';
import { Body, Button, Card, Check, ErrorText, Field, Label, Notice } from './ui';
import { colors, radius, space } from '../lib/theme';

export const KINDS: { key: ReportKind; label: string; hint: string }[] = [
  { key: 'daily', label: 'Daily', hint: 'each working day' },
  { key: 'weekly', label: 'Weekly', hint: 'end of each week' },
  { key: 'monthly', label: 'Monthly', hint: 'end of each month' },
];
const DUE_WORD: Record<ReportKind, string> = { daily: 'today', weekly: 'this week', monthly: 'this month' };

function niceDate(iso: string | null) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${d} ${months[m - 1]} ${y}`;
}

/** "Daily and weekly updates · on time 92% of the time" — shown on quote cards. */
export function PromiseLine({ text, record }: { text?: string; record?: ReportRecord | null }) {
  if (!text && !record) return null;
  return (
    <View style={styles.promise}>
      <Ionicons name="calendar-outline" size={17} color={colors.chalkDeep} />
      <Text style={{ flex: 1, fontSize: 14.5, color: colors.ink, lineHeight: 20 }}>
        {text ? <Text style={{ fontWeight: '700' }}>Promises {text.toLowerCase()}</Text> : <Text>No progress updates promised</Text>}
        {record ? <Text style={{ color: colors.ink2 }}>{` · kept ${record.pct}% on time across ${record.jobs} job${record.jobs === 1 ? '' : 's'}`}</Text> : null}
      </Text>
    </View>
  );
}

/** Daily / weekly / monthly toggles for the quote form. */
export function PlanPicker({ value, onChange }: { value: ReportKind[]; onChange: (v: ReportKind[]) => void }) {
  return (
    <View style={{ marginBottom: space.lg }}>
      <Text style={styles.fieldLabel}>Progress updates if you’re hired</Text>
      <Text style={styles.hint}>Customers see this next to your price, and your record of keeping it builds up on your profile.</Text>
      {KINDS.map((k) => (
        <Check key={k.key} label={`${k.label} — ${k.hint}`} value={value.includes(k.key)}
          onChange={(on) => onChange(KINDS.map((x) => x.key).filter((x) => (x === k.key ? on : value.includes(x))))} />
      ))}
    </View>
  );
}

function ScoreChips({ progress }: { progress: Progress }) {
  const entries = Object.entries(progress.score) as [ReportKind, NonNullable<Progress['score'][ReportKind]>][];
  if (!entries.length) return null;
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: space.md }}>
      {entries.map(([kind, s]) => {
        const good = s.expected > 0 && s.kept === s.expected;
        const warn = s.expected > 0 && s.kept / s.expected < 0.8;
        return (
          <View key={kind} style={[styles.chip, good && { backgroundColor: colors.pineWash }, warn && { backgroundColor: colors.paintWash }]}
            accessible accessibilityLabel={`${s.label}: ${s.kept} of ${s.expected} on time${s.due ? `, due ${DUE_WORD[kind]}` : ''}`}>
            <Text style={styles.chipNum}>{s.kept}<Text style={styles.chipOf}>/{s.expected}</Text></Text>
            <Text style={styles.chipText}>{s.label} update{s.expected === 1 ? '' : 's'} on time</Text>
            {s.due ? <Text style={styles.chipDue}>Due {DUE_WORD[kind]}</Text> : null}
          </View>
        );
      })}
    </View>
  );
}

/**
 * The progress-updates part of a hired job. Trades can post updates, set the
 * start date and mark the work finished; customers read and can mark it finished.
 */
export function ProgressSection({ progress, role, jobId, onChanged }: {
  progress: Progress; role: 'trade' | 'customer'; jobId: number; onChanged: () => void;
}) {
  const finished = !!progress.work_done_on;
  const [busy, setBusy] = useState<string | null>(null);

  function finish() {
    Alert.alert(role === 'trade' ? 'Mark the work finished?' : 'Is the work finished?',
      role === 'trade' ? 'Updates stop being due, and the customer is told.' : 'Updates stop being due, and the trade is told.', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Yes, it’s finished', onPress: async () => {
            setBusy('finish');
            try {
              const res = role === 'trade' ? await api.tradeFinish(jobId) : await api.customerFinish(jobId);
              Alert.alert(res.message || 'Marked as finished.');
              onChanged();
            } catch (e: any) {
              Alert.alert('That didn’t go through', e.message);
            } finally {
              setBusy(null);
            }
          },
        },
      ]);
  }

  return (
    <View>
      <Label style={{ marginTop: space.md, marginBottom: 8 }}>Progress updates</Label>
      <Card>
        {progress.report_plan.length ? (
          <Body muted style={{ fontSize: 15, marginBottom: 10 }}>
            {role === 'trade' ? 'You promised ' : 'They promised '}
            <Text style={{ fontWeight: '700', color: colors.ink }}>{progress.report_plan_text.toLowerCase()}</Text> when {role === 'trade' ? 'you' : 'they'} quoted.
          </Body>
        ) : (
          <Body muted style={{ fontSize: 15, marginBottom: 10 }}>
            No regular updates were promised{role === 'trade' ? ', but updates still help the customer.' : '. The trade can still post updates here.'}
          </Body>
        )}
        <ScoreChips progress={progress} />
        <Text style={{ fontSize: 14, color: colors.ink2 }}>
          {progress.work_started_on ? `Work ${progress.work_started_on > progress.today ? 'starts' : 'started'} ${niceDate(progress.work_started_on)}` : ''}
          {finished ? ` · finished ${niceDate(progress.work_done_on)}` : ''}
        </Text>
        {role === 'trade' && !finished ? <StartDate jobId={jobId} progress={progress} onChanged={onChanged} /> : null}
      </Card>

      {role === 'trade' && !finished ? <UpdateForm jobId={jobId} progress={progress} onPosted={onChanged} /> : null}

      {progress.updates.length === 0 ? (
        <Notice tone="muted">{role === 'trade' ? 'No updates yet. A couple of lines and a photo is plenty.' : 'No updates yet. You’ll get a notification when one arrives.'}</Notice>
      ) : progress.updates.map((u) => (
        <Card key={u.id}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 }}>
            <Text style={{ fontSize: 13, fontWeight: '700', color: colors.ink2 }}>
              {niceDate(u.local_date).toUpperCase()}{u.kinds.length ? ` · ${u.kinds.map((k) => k.toUpperCase()).join(' + ')}` : ''}
            </Text>
            <Text style={{ fontSize: 13, color: colors.ink3 }}>{ago(u.created_at)}</Text>
          </View>
          <Body selectable>{u.body}</Body>
          {u.photos.length ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 10 }}>
              {u.photos.map((p, i) => (
                <Image key={p} source={{ uri: p }} accessibilityLabel={`Update photo ${i + 1}`}
                  style={{ width: 120, height: 120, borderRadius: radius, marginRight: 8, backgroundColor: colors.ruleSoft }} />
              ))}
            </ScrollView>
          ) : null}
        </Card>
      ))}

      {!finished ? (
        <Button title={role === 'trade' ? 'Work finished' : 'The work is finished'} icon="checkmark-done-outline" variant="quiet"
          loading={busy === 'finish'} onPress={finish} />
      ) : null}
    </View>
  );
}

function StartDate({ jobId, progress, onChanged }: { jobId: number; progress: Progress; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(progress.work_started_on || progress.today);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.setStart(jobId, value.trim());
      setEditing(false);
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!editing) return <Button title="Change start date" variant="ghost" small onPress={() => setEditing(true)} style={{ marginTop: 6 }} />;
  return (
    <View style={{ marginTop: space.md }}>
      <ErrorText>{error}</ErrorText>
      <Field label="Start date" value={value} onChangeText={setValue} placeholder="YYYY-MM-DD" autoCapitalize="none"
        autoCorrect={false} keyboardType="numbers-and-punctuation" hint={`Year-month-day, e.g. ${progress.today}`} />
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Button title="Save" small loading={busy} onPress={save} style={{ flex: 1 }} />
        <Button title="Cancel" variant="quiet" small onPress={() => setEditing(false)} style={{ flex: 1 }} />
      </View>
    </View>
  );
}

function UpdateForm({ jobId, progress, onPosted }: { jobId: number; progress: Progress; onPosted: () => void }) {
  const [body, setBody] = useState('');
  const [kinds, setKinds] = useState<ReportKind[]>(progress.due);
  const [photos, setPhotos] = useState<PhotoInput[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dueKey = progress.due.join(',');

  // Whatever is due counts by default
  useEffect(() => { setKinds(progress.due); }, [dueKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function add(source: 'camera' | 'library') {
    const added = await pickPhotos(source, progress.max_photos - photos.length);
    if (added.length) setPhotos((old) => [...old, ...added].slice(0, progress.max_photos));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.postUpdate(jobId, body.trim(), kinds, photos);
      setBody('');
      setPhotos([]);
      Alert.alert('Update posted', res.message || 'The customer has been told.');
      onPosted();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <Text style={{ fontSize: 17, fontWeight: '800', color: colors.ink, marginBottom: 10 }}>Post an update</Text>
      <ErrorText>{error}</ErrorText>
      <Field label="What got done, what’s next" value={body} onChangeText={setBody} multiline
        placeholder="e.g. Framing is up and the roof’s on. Cladding starts Monday." style={{ minHeight: 100 }} />
      {progress.report_plan.length ? (
        <View style={{ marginBottom: space.sm }}>
          <Text style={styles.fieldLabel}>Counts as</Text>
          {progress.report_plan.map((k) => (
            <Check key={k} label={`${KINDS.find((x) => x.key === k)?.label} update${progress.due.includes(k) ? ` (due ${DUE_WORD[k]})` : ''}`}
              value={kinds.includes(k)} onChange={(on) => setKinds((old) => (on ? [...old, k] : old.filter((x) => x !== k)))} />
          ))}
        </View>
      ) : null}
      {photos.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          {photos.map((p, i) => (
            <View key={p.uri}>
              <Image source={{ uri: p.uri }} style={{ width: 84, height: 84, borderRadius: radius }} accessibilityLabel={`Photo ${i + 1}`} />
              <Pressable onPress={() => setPhotos((old) => old.filter((x) => x.uri !== p.uri))} hitSlop={8} style={styles.remove}
                accessibilityRole="button" accessibilityLabel={`Remove photo ${i + 1}`}>
                <Ionicons name="close" size={15} color="#fff" />
              </Pressable>
            </View>
          ))}
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Button title="Camera" icon="camera-outline" variant="quiet" small onPress={() => add('camera')} style={{ flex: 1 }} />
        <Button title="Library" icon="images-outline" variant="quiet" small onPress={() => add('library')} style={{ flex: 1 }} />
      </View>
      <Button title="Post update" icon="send" loading={busy} disabled={body.trim().length < 15} onPress={submit} />
    </Card>
  );
}

const styles = StyleSheet.create({
  fieldLabel: { fontSize: 15, fontWeight: '600', color: colors.ink, marginBottom: 6 },
  hint: { color: colors.ink2, fontSize: 13.5, lineHeight: 19, marginBottom: 6 },
  promise: { flexDirection: 'row', gap: 8, alignItems: 'flex-start', backgroundColor: colors.chalkWash, borderRadius: 8,
    padding: 10, marginTop: 10 },
  chip: { backgroundColor: colors.slab, borderRadius: radius, paddingVertical: 8, paddingHorizontal: 12, minWidth: 110 },
  chipNum: { fontSize: 22, fontWeight: '800', color: colors.ink, fontVariant: ['tabular-nums'] },
  chipOf: { fontSize: 14, fontWeight: '600', color: colors.ink2 },
  chipText: { fontSize: 12.5, color: colors.ink2 },
  chipDue: { fontSize: 12.5, fontWeight: '700', color: colors.paintInk, marginTop: 2 },
  remove: { position: 'absolute', top: 3, right: 3, width: 24, height: 24, borderRadius: 12, backgroundColor: 'rgba(22,32,43,.75)',
    alignItems: 'center', justifyContent: 'center' },
});
