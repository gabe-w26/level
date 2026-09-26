/**
 * Asking to do extra work, on a phone — which is where it actually happens.
 *
 * A tradie lifts a board, finds three rotten joists, and says "this needs doing,
 * it's another $800". On the phone that's a verbal yes nobody can prove. This
 * makes it a request with a price that the other person presses yes or no to,
 * and keeps the answer.
 *
 * The rules are the website's, not looser ones because it's a smaller screen:
 * either side can ask, a price is a price, the person who asked can't be the one
 * who agrees, and an answered request stays answered. The server enforces all of
 * that — this only avoids offering buttons that would be refused.
 */
import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Ask, AskInput, ExtraAgreed } from '../lib/api';
import { Button, Choice, ErrorText, Field, Pill } from './ui';
import { colors, radius, space } from '../lib/theme';

const money = (n: number) => `$${n.toLocaleString('en-NZ')}`;

const TONE: Record<Ask['status'], 'chalk' | 'pine' | 'muted'> = {
  asked: 'chalk', accepted: 'pine', declined: 'muted', withdrawn: 'muted',
};

export function ExtraWorkList({ asks, extra, busy, onAnswer }: {
  asks: Ask[];
  extra: ExtraAgreed | null;
  busy: boolean;
  onAnswer: (id: number, decision: 'accepted' | 'declined' | 'withdrawn') => void;
}) {
  if (!asks.length) return null;
  return (
    <View style={{ gap: 8 }}>
      {extra ? (
        <View style={styles.agreed}>
          <Ionicons name="checkmark-circle" size={17} color={colors.ink} />
          <Text style={styles.agreedText}>
            {extra.count} extra {extra.count === 1 ? 'job' : 'jobs'} agreed —{' '}
            {money(extra.total_incl_gst)} including GST
          </Text>
        </View>
      ) : null}

      {asks.map((a) => (
        <View key={a.id} style={styles.card}>
          <View style={styles.head}>
            <Text style={styles.title}>{a.title}</Text>
            <Pill label={a.status_label} tone={TONE[a.status]} />
          </View>
          <Text style={styles.price}>{a.price}</Text>
          <Text style={styles.detail}>{a.detail}</Text>

          {a.status === 'asked' && !a.mine ? (
            <>
              <View style={styles.buttons}>
                <Button title="Agree to this" icon="checkmark" variant="pine" small loading={busy}
                  onPress={() => onAnswer(a.id, 'accepted')} />
                <Button title="No thanks" variant="quiet" small disabled={busy}
                  onPress={() => onAnswer(a.id, 'declined')} />
              </View>
              <Text style={styles.small}>
                Level writes down what the two of you agreed. It doesn’t hold the money or take a
                side if it goes wrong.
              </Text>
            </>
          ) : null}

          {a.status === 'asked' && a.mine ? (
            <View style={styles.buttons}>
              <Text style={styles.waiting}>Waiting on them</Text>
              <Button title="Take it back" variant="quiet" small disabled={busy}
                onPress={() => onAnswer(a.id, 'withdrawn')} />
            </View>
          ) : null}
        </View>
      ))}
    </View>
  );
}

export function AskForm({ busy, error, onSubmit, onCancel }: {
  busy: boolean;
  error: string | null;
  onSubmit: (fields: AskInput) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState('');
  const [amount, setAmount] = useState('');
  const [gst, setGst] = useState<'incl' | 'excl'>('incl');
  const [detail, setDetail] = useState('');

  // Mirrors the server's rules so somebody isn't told off after tapping Send.
  // The server still decides — this just doesn't offer a button that will fail.
  const priced = Number(amount.replace(/[^0-9.]/g, '')) > 0;
  const ready = title.trim().length > 2 && priced && detail.trim().length >= 20;

  return (
    <View style={styles.form}>
      <Text style={styles.formTitle}>Ask about extra work</Text>
      <Text style={styles.small}>
        Something you’ve found, or something else you’d like done. It goes across with a price for
        the other person to agree to, and the answer is kept.
      </Text>
      <ErrorText>{error}</ErrorText>

      <Field label="What needs doing" value={title} onChangeText={setTitle}
        placeholder="Replace three rotten joists" maxLength={120} />
      <Field label="What it comes to" value={amount} onChangeText={setAmount}
        placeholder="800" keyboardType="numeric" />
      <Choice label="GST" value={gst} onChange={setGst}
        options={[{ key: 'incl', label: 'Includes GST' }, { key: 'excl', label: 'Plus GST' }]} />
      <Field label="Why it needs doing" value={detail} onChangeText={setDetail} multiline
        hint="The other person has to decide on this, so say enough that they can."
        placeholder="The joists under the worst boards have gone. They need replacing before the new decking goes down."
        maxLength={1200} style={{ minHeight: 96 }} />

      <View style={styles.buttons}>
        <Button title="Send the request" icon="send" loading={busy} disabled={!ready}
          onPress={() => onSubmit({ title: title.trim(), amount, gst, detail: detail.trim() })} />
        <Button title="Cancel" variant="quiet" disabled={busy} onPress={onCancel} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.paper, borderRadius: radius, padding: space.md,
    borderWidth: StyleSheet.hairlineWidth, borderColor: colors.rule,
  },
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  title: { flex: 1, fontSize: 16, fontWeight: '700', color: colors.ink },
  price: { fontSize: 15, fontWeight: '700', color: colors.ink, marginTop: 2 },
  detail: { fontSize: 14.5, lineHeight: 20, color: colors.ink2, marginTop: 6 },
  buttons: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 10, flexWrap: 'wrap' },
  waiting: { flex: 1, fontSize: 13.5, color: colors.ink3 },
  small: { fontSize: 12.5, lineHeight: 17, color: colors.ink3, marginTop: 8 },
  agreed: {
    flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: colors.slab,
    borderRadius: radius, paddingHorizontal: space.md, paddingVertical: 10,
  },
  agreedText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.ink },
  form: {
    backgroundColor: colors.paper, borderRadius: radius, padding: space.md, gap: 4,
    borderWidth: StyleSheet.hairlineWidth, borderColor: colors.rule,
  },
  formTitle: { fontSize: 17, fontWeight: '700', color: colors.ink },
});
