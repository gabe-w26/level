/**
 * The site section: details, notes and the pre-start check.
 *
 * Shown on a job once someone is hired, to both sides. The tradie gets the
 * check; the customer only reads it. Mirrors the website's `_site.html` so the
 * two never drift — if you change the wording here, change it there.
 */
import React, { useState } from 'react';
import { Alert, Image, Text, View } from 'react-native';
import { api, Site, SiteCheck } from '../lib/api';
import { ago } from '../lib/format';
import { Body, Button, Card, Label, Notice, Pill, Row } from './ui';
import { colors, radius, space } from '../lib/theme';

export function SiteSection({ site, jobId, role, onChanged }: {
  site: Site; jobId: number; role: 'customer' | 'trade'; onChanged: () => void;
}) {
  return (
    <View>
      <Label style={{ marginTop: space.lg, marginBottom: 8 }}>The site</Label>
      <SiteDetails site={site} jobId={jobId} onChanged={onChanged} />
      {role === 'trade' ? <PreStartCheck site={site} jobId={jobId} onChanged={onChanged} /> : null}
      {site.checks.length ? <Checks checks={site.checks} role={role} /> : null}
      <SiteNotes site={site} jobId={jobId} role={role} onChanged={onChanged} />
    </View>
  );
}

function SiteDetails({ site, jobId, onChanged }: { site: Site; jobId: number; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(site.fields.map((f) => [f.key, f.value])));
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      const res = await api.saveSite(jobId, values);
      Alert.alert(res.message);
      onChanged();
      setOpen(false);
    } catch (e: any) {
      Alert.alert(e.message || 'That didn’t save.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text style={{ fontWeight: '800', color: colors.ink, fontSize: 16 }}>Getting in</Text>
        <Text style={{ fontSize: 13, color: colors.ink3 }}>{site.filled} of {site.fields.length} filled in</Text>
      </View>
      {!open ? (
        <>
          {site.fields.filter((f) => f.value).map((f) => <Row key={f.key} label={f.label} value={f.value} />)}
          {site.filled === 0 ? (
            <Body style={{ marginTop: 6, color: colors.ink2 }}>
              Nothing filled in yet. Side gate codes, parking, the dog — the things that waste a trip.
            </Body>
          ) : null}
          <Button title="Edit site details" icon="create-outline" variant="quiet" small onPress={() => setOpen(true)} />
        </>
      ) : (
        <>
          {site.fields.map((f) => (
            <View key={f.key} style={{ marginTop: 10 }}>
              <Text style={{ fontWeight: '700', color: colors.ink }}>{f.label}</Text>
              <Text style={{ fontSize: 13, color: colors.ink3, marginBottom: 4 }}>{f.hint}</Text>
              <Field
                value={values[f.key] ?? ''}
                editable={f.key !== 'address'}
                onChangeText={(t) => setValues({ ...values, [f.key]: t })}
              />
            </View>
          ))}
          <Button title="Save site details" variant="pine" small loading={busy} onPress={save} />
          <Button title="Cancel" variant="ghost" small onPress={() => setOpen(false)} />
        </>
      )}
    </Card>
  );
}

function PreStartCheck({ site, jobId, onChanged }: { site: Site; jobId: number; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [hazards, setHazards] = useState('');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);

  async function save() {
    const missing = site.check_items.find((i) => !answers[i.key]);
    if (missing) { Alert.alert('Still to answer', missing.question); return; }
    setBusy(true);
    try {
      const res = await api.saveSiteCheck(jobId, answers, hazards, notes);
      Alert.alert(res.message);
      setAnswers({}); setHazards(''); setNotes(''); setOpen(false);
      onChanged();
    } catch (e: any) {
      Alert.alert(e.message || 'That didn’t save.');
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Card>
        <Text style={{ fontWeight: '800', color: colors.ink, fontSize: 16 }}>Pre-start check</Text>
        <Body style={{ marginTop: 4, color: colors.ink2 }}>
          A record of what you found when you got there. Not advice, and no substitute for your own
          duties under the Health and Safety at Work Act.
        </Body>
        <Button title="Do a pre-start check" icon="clipboard-outline" variant="quiet" small
          onPress={() => setOpen(true)} />
      </Card>
    );
  }

  return (
    <Card>
      <Text style={{ fontWeight: '800', color: colors.ink, fontSize: 16, marginBottom: 4 }}>Pre-start check</Text>
      {site.check_items.map((item) => (
        <View key={item.key} style={{ marginTop: 14 }}>
          <Text style={{ fontWeight: '700', color: colors.ink }}>{item.question}</Text>
          <Text style={{ fontSize: 13, color: colors.ink3, marginBottom: 6 }}>{item.why}</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {[['yes', 'Yes'], ['no', 'No'], ['na', 'N/A']].map(([value, word]) => {
              const on = answers[item.key] === value;
              return (
                <Text
                  key={value}
                  onPress={() => setAnswers({ ...answers, [item.key]: value })}
                  // Without a role and a state, a screen reader reads three
                  // words and gives no way to tell which one is chosen — the
                  // tint alone carries it, which is exactly what colour must
                  // never do on its own.
                  accessibilityRole="radio"
                  accessibilityState={{ selected: on, checked: on }}
                  accessibilityLabel={`${word} — ${item.question}`}
                  style={{
                    flex: 1, textAlign: 'center', paddingVertical: 14, borderRadius: radius,
                    overflow: 'hidden', fontWeight: '700',
                    // Chosen is also 1.5pt heavier, so it reads without colour.
                    borderWidth: on ? 2.5 : 1.5,
                    borderColor: on ? (value === 'no' ? colors.paintInk : colors.pine) : colors.rule,
                    backgroundColor: on ? (value === 'no' ? colors.paintWash : colors.pineWash) : colors.paper,
                    color: on ? (value === 'no' ? colors.paintInk : colors.pine) : colors.ink2,
                  }}>
                  {on ? '✓ ' : ''}{word}
                </Text>
              );
            })}
          </View>
        </View>
      ))}
      <View style={{ marginTop: 14 }}>
        <Text style={{ fontWeight: '700', color: colors.ink }}>Hazards you found</Text>
        <Text style={{ fontSize: 13, color: colors.ink3, marginBottom: 4 }}>What they are and what you did about them</Text>
        <Field value={hazards} onChangeText={setHazards} />
      </View>
      <View style={{ marginTop: 10 }}>
        <Text style={{ fontWeight: '700', color: colors.ink, marginBottom: 4 }}>Anything else</Text>
        <Field value={notes} onChangeText={setNotes} />
      </View>
      <Button title="Save the check" variant="pine" small loading={busy} onPress={save} />
      <Button title="Cancel" variant="ghost" small onPress={() => setOpen(false)} />
    </Card>
  );
}

function Checks({ checks, role }: { checks: SiteCheck[]; role: 'customer' | 'trade' }) {
  return (
    <Card>
      <Text style={{ fontWeight: '800', color: colors.ink, fontSize: 16, marginBottom: 6 }}>
        {role === 'customer' ? 'Their site checks' : 'Checks you’ve done'}
      </Text>
      {checks.map((c) => (
        <View key={c.id} style={{ paddingVertical: 10, borderTopWidth: 1, borderTopColor: colors.ruleSoft }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text style={{ fontWeight: '700', color: colors.ink }}>{ago(c.created_at)}</Text>
            <Pill label={c.flags.length ? `${c.flags.length} to sort` : 'Nothing marked “no”'}
              tone={c.flags.length ? 'paint' : 'muted'} />
          </View>
          {c.flags.map((f, i) => (
            <View key={i} style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
              <Text style={{ color: colors.paint }}>•</Text>
              <Text style={{ flex: 1, color: colors.ink2 }}>{f}</Text>
            </View>
          ))}
          {c.hazards ? <Row label="Hazards" value={c.hazards} /> : null}
          {c.notes ? <Body style={{ marginTop: 6, color: colors.ink2 }}>{c.notes}</Body> : null}
        </View>
      ))}
      <Text style={{ fontSize: 13, color: colors.ink3, marginTop: 8 }}>
        A check with nothing marked “no” is a record of what the tradie said they found. It isn’t Level
        saying the site is safe.
      </Text>
    </Card>
  );
}

function SiteNotes({ site, jobId, role, onChanged }: {
  site: Site; jobId: number; role: 'customer' | 'trade'; onChanged: () => void;
}) {
  const [body, setBody] = useState('');
  const [isPrivate, setPrivate] = useState(false);
  const [busy, setBusy] = useState(false);

  async function add() {
    if (body.trim().length < 2) return;
    setBusy(true);
    try {
      await api.addSiteNote(jobId, body.trim(), isPrivate);
      setBody(''); setPrivate(false);
      onChanged();
    } catch (e: any) {
      Alert.alert(e.message || 'That didn’t save.');
    } finally {
      setBusy(false);
    }
  }

  async function remove(noteId: number) {
    try {
      await api.deleteSiteNote(jobId, noteId);
      onChanged();
    } catch (e: any) {
      Alert.alert(e.message || 'That didn’t delete.');
    }
  }

  return (
    <Card>
      <Text style={{ fontWeight: '800', color: colors.ink, fontSize: 16 }}>Site notes</Text>
      <Body style={{ marginTop: 4, color: colors.ink2 }}>
        What was done, what came up, what got decided.
      </Body>
      <Field value={body} onChangeText={setBody}
        placeholder={role === 'trade'
          ? 'Lifted the old boards, three joists need replacing.'
          : 'Left the side gate unlocked for you.'} />
      {role === 'trade' ? (
        <Text
          onPress={() => setPrivate(!isPrivate)}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: isPrivate }}
          accessibilityLabel="Keep this note to myself"
          style={{ color: colors.ink2, paddingVertical: 12 }}>
          {isPrivate ? '☑' : '☐'} Keep this one to myself — the customer won’t see it
        </Text>
      ) : null}
      <Button title="Add note" variant="quiet" small loading={busy} onPress={add} />

      {site.notes.map((n) => (
        <View key={n.id} style={{
          paddingVertical: 12, borderTopWidth: 1, borderTopColor: colors.ruleSoft,
          ...(n.shared ? {} : { borderLeftWidth: 3, borderLeftColor: colors.rule, paddingLeft: 10 }),
        }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <Text style={{ fontWeight: '700', color: colors.ink }}>{n.who}</Text>
            <Text style={{ fontSize: 13, color: colors.ink3 }}>
              {ago(n.created_at)}{n.shared ? '' : ' · only you'}
            </Text>
          </View>
          <Body style={{ marginTop: 2 }}>{n.body}</Body>
          {n.photos.length ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
              {n.photos.map((url) => (
                <Image key={url} source={{ uri: url }}
                  style={{ width: 84, height: 84, borderRadius: 8, backgroundColor: colors.slab }} />
              ))}
            </View>
          ) : null}
          {n.can_delete ? (
            <Button title="Delete" variant="ghost" small onPress={() => remove(n.id)} />
          ) : null}
        </View>
      ))}
      {!site.notes.length ? (
        <Text style={{ fontSize: 13, color: colors.ink3, marginTop: 10 }}>Nothing yet.</Text>
      ) : null}
    </Card>
  );
}

/** A plain multi-line box. Kept local so the site section matches itself. */
function Field({ value, onChangeText, placeholder, editable = true }: {
  value: string; onChangeText: (t: string) => void; placeholder?: string; editable?: boolean;
}) {
  const { TextInput } = require('react-native');
  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.ink3}
      editable={editable}
      multiline
      style={{
        borderWidth: 1.5, borderColor: colors.rule, borderRadius: radius, padding: 12, marginTop: 6,
        minHeight: 64, fontSize: 16, color: editable ? colors.ink : colors.ink3,
        backgroundColor: editable ? colors.paper : colors.slab, textAlignVertical: 'top',
      }}
    />
  );
}
