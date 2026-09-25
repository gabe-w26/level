/**
 * The optional breakdown on a quote, on a phone.
 *
 * Same rules as the website: each line is rounded to the cent so the column
 * adds up to the total underneath it, GST is worked out once at the bottom from
 * the incl./plus choice, and once anything is priced the breakdown *is* the
 * price — the plain price box goes quiet rather than sitting there disagreeing.
 *
 * Deliberately a short list of wide rows rather than a table. A table needs
 * four columns to make sense and there isn't room for four columns on a phone
 * a tradie is holding in one hand on a site.
 */
import React from 'react';
import { Text, TextInput, View } from 'react-native';
import { QuoteItem } from '../lib/api';
import { Button } from './ui';
import { colors, radius } from '../lib/theme';

export const BLANK: QuoteItem = { description: '', qty: null, unit: null, unit_price: null };

const money = (n: number) =>
  n.toLocaleString('en-NZ', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** A number from whatever was typed, or null. Never throws on rubbish. */
export function num(text: string | null | undefined): number | null {
  if (text == null) return null;
  const cleaned = String(text).replace(/[^0-9.\-]/g, '');
  if (!cleaned) return null;
  const value = parseFloat(cleaned);
  return isNaN(value) ? null : value;
}

/** One line's money, rounded to the cent. A line with no quantity counts once. */
export function lineTotal(item: QuoteItem): number | null {
  if (item.unit_price == null) return null;
  return Math.round(item.unit_price * (item.qty == null ? 1 : item.qty) * 100) / 100;
}

export function totals(items: QuoteItem[], gstIncluded: boolean) {
  const priced = items.map(lineTotal).filter((t): t is number => t != null);
  if (!priced.length) return { subtotal: null, gst: null, total: null };
  const summed = Math.round(priced.reduce((a, b) => a + b, 0) * 100) / 100;
  const subtotal = gstIncluded ? summed / 1.15 : summed;
  const gst = subtotal * 0.15;
  return { subtotal, gst, total: gstIncluded ? summed : subtotal + gst };
}

export function LineItems({ items, units, gstIncluded, onChange }: {
  items: QuoteItem[];
  units: string[];
  gstIncluded: boolean;
  onChange: (items: QuoteItem[]) => void;
}) {
  const t = totals(items, gstIncluded);
  const set = (i: number, patch: Partial<QuoteItem>) =>
    onChange(items.map((item, n) => (n === i ? { ...item, ...patch } : item)));

  return (
    <View style={{ marginTop: 18 }}>
      <Text style={{ fontWeight: '700', color: colors.ink, fontSize: 16 }}>Break the price down</Text>
      <Text style={{ fontSize: 13, color: colors.ink3, marginBottom: 4 }}>
        Optional. Customers ask where the money goes — this answers it, and the total becomes your price.
      </Text>

      {items.map((item, i) => {
        const total = lineTotal(item);
        return (
          <View key={i} style={{
            borderWidth: 1, borderColor: colors.ruleSoft, borderRadius: radius,
            padding: 10, marginTop: 8, backgroundColor: colors.paper,
          }}>
            <TextInput
              value={item.description}
              onChangeText={(v) => set(i, { description: v })}
              placeholder="Labour, two builders"
              placeholderTextColor={colors.ink3}
              maxLength={200}
              style={{ fontSize: 16, color: colors.ink, paddingVertical: 6 }}
            />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 }}>
              <Small value={item.qty == null ? '' : String(item.qty)} placeholder="Qty"
                onChangeText={(v) => set(i, { qty: num(v) })} width={62} numeric />
              <Small value={item.unit || ''} placeholder="unit"
                onChangeText={(v) => set(i, { unit: v || null })} width={78} />
              <Text style={{ color: colors.ink3 }}>×  $</Text>
              <Small value={item.unit_price == null ? '' : String(item.unit_price)} placeholder="Rate"
                onChangeText={(v) => set(i, { unit_price: num(v) })} width={78} numeric />
              <Text style={{ flex: 1, textAlign: 'right', fontWeight: '700', color: colors.ink }}>
                {total == null ? '' : `$${money(total)}`}
              </Text>
            </View>
            {items.length > 1 ? (
              <Text onPress={() => onChange(items.filter((_, n) => n !== i))}
                style={{ color: colors.ink3, marginTop: 6, fontSize: 13 }}>
                Remove this line
              </Text>
            ) : null}
          </View>
        );
      })}

      <Button title="Add a line" icon="add-outline" variant="quiet" small
        onPress={() => onChange([...items, { ...BLANK }])} />

      {units.length ? (
        <Text style={{ fontSize: 12, color: colors.ink3, marginTop: -2 }}>
          Units people use: {units.join(', ')}
        </Text>
      ) : null}

      {t.total != null ? (
        <View style={{ marginTop: 12, padding: 12, backgroundColor: colors.slab, borderRadius: radius }}>
          <Line label="Before GST" value={t.subtotal!} />
          <Line label="GST" value={t.gst!} />
          <Line label="Total including GST" value={t.total} bold />
          <Text style={{ fontSize: 13, color: colors.ink3, marginTop: 6 }}>
            This is what gets sent as your price. GST is worked out once here, from the
            “{gstIncluded ? 'Includes GST' : 'Plus GST'}” choice above — not on each line.
          </Text>
        </View>
      ) : null}
    </View>
  );
}

function Line({ label, value, bold }: { label: string; value: number; bold?: boolean }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: bold ? 6 : 2 }}>
      <Text style={{ color: bold ? colors.ink : colors.ink2, fontWeight: bold ? '800' : '400' }}>{label}</Text>
      <Text style={{ color: colors.ink, fontWeight: bold ? '800' : '400', fontVariant: ['tabular-nums'] }}>
        ${money(value)}
      </Text>
    </View>
  );
}

function Small({ value, onChangeText, placeholder, width, numeric }: {
  value: string; onChangeText: (v: string) => void; placeholder: string; width: number; numeric?: boolean;
}) {
  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.ink3}
      keyboardType={numeric ? 'numeric' : 'default'}
      style={{
        width, borderWidth: 1, borderColor: colors.rule, borderRadius: 7,
        paddingHorizontal: 8, paddingVertical: 8, fontSize: 15, color: colors.ink,
      }}
    />
  );
}
