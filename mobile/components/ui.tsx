import React from 'react';
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, TextInputProps,
  View, ViewStyle, StyleProp,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, font, radius, space, TAP } from '../lib/theme';

type Tone = 'chalk' | 'pine' | 'paint' | 'muted' | 'danger';

// ── Layout ────────────────────────────────────────────────────────────────────

export function Screen({ children, refreshing, onRefresh, style, keyboard }: {
  children: React.ReactNode; refreshing?: boolean; onRefresh?: () => void; style?: StyleProp<ViewStyle>;
  keyboard?: boolean;
}) {
  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[styles.screenContent, style]}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode={keyboard ? 'interactive' : 'on-drag'}
      // iOS-only. Android gets the same effect from softwareKeyboardLayoutMode
      // 'resize' in app.json, which resizes the window instead — set there
      // explicitly rather than left to a default that could change.
      automaticallyAdjustKeyboardInsets
      refreshControl={onRefresh ? (
        <RefreshControl
          refreshing={!!refreshing}
          onRefresh={onRefresh}
          tintColor={colors.chalk}          // iOS
          colors={[colors.chalk]}           // Android reads this one instead
        />
      ) : undefined}
    >
      {children}
    </ScrollView>
  );
}

export function Card({ children, style, onPress }: { children: React.ReactNode; style?: StyleProp<ViewStyle>; onPress?: () => void }) {
  if (onPress) {
    return (
      <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && styles.pressed, style]} accessibilityRole="button">
        {children}
      </Pressable>
    );
  }
  return <View style={[styles.card, style]}>{children}</View>;
}

export function Title({ children, size = 26 }: { children: React.ReactNode; size?: number }) {
  return <Text style={[font.display, { fontSize: size, lineHeight: size * 1.12, marginBottom: space.sm }]}>{children}</Text>;
}

export function Label({ children, style }: { children: React.ReactNode; style?: any }) {
  return <Text style={[font.label, style]}>{children}</Text>;
}

export function Body({ children, muted, style, selectable }: { children: React.ReactNode; muted?: boolean; style?: any; selectable?: boolean }) {
  return <Text selectable={selectable} style={[styles.body, muted && { color: colors.ink2 }, style]}>{children}</Text>;
}

export function Row({ label, value }: { label: string; value?: React.ReactNode }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      {typeof value === 'string' || typeof value === 'number'
        ? <Text style={styles.rowValue} selectable>{value}</Text>
        : <View style={{ flex: 1 }}>{value}</View>}
    </View>
  );
}

// ── Buttons ───────────────────────────────────────────────────────────────────

export function Button({ title, onPress, variant = 'primary', icon, loading, disabled, small, style }: {
  title: string; onPress: () => void; variant?: 'primary' | 'quiet' | 'pine' | 'danger' | 'ghost';
  icon?: keyof typeof Ionicons.glyphMap; loading?: boolean; disabled?: boolean; small?: boolean; style?: StyleProp<ViewStyle>;
}) {
  const v = BUTTONS[variant];
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      accessibilityRole="button"
      accessibilityLabel={title}
      style={({ pressed }) => [
        styles.button, { backgroundColor: v.bg, borderColor: v.border }, small && styles.buttonSmall,
        pressed && { opacity: 0.8 }, (disabled || loading) && { opacity: 0.5 }, style,
      ]}
    >
      {loading ? <ActivityIndicator color={v.fg} /> : (
        <>
          {icon ? <Ionicons name={icon} size={small ? 18 : 20} color={v.fg} style={{ marginRight: 8 }} /> : null}
          <Text style={[styles.buttonText, { color: v.fg }, small && { fontSize: 15 }]}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

const BUTTONS = {
  primary: { bg: colors.chalk, fg: '#fff', border: colors.chalk },
  pine: { bg: colors.pine, fg: '#fff', border: colors.pine },
  danger: { bg: colors.danger, fg: '#fff', border: colors.danger },
  quiet: { bg: colors.paper, fg: colors.ink, border: colors.rule },
  ghost: { bg: 'transparent', fg: colors.chalkDeep, border: 'transparent' },
};

export function LinkRow({ title, icon, onPress, detail, danger }: {
  title: string; icon: keyof typeof Ionicons.glyphMap; onPress: () => void; detail?: string; danger?: boolean;
}) {
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.linkRow, pressed && { backgroundColor: colors.slab }]}>
      <Ionicons name={icon} size={22} color={danger ? colors.danger : colors.ink2} />
      <Text style={[styles.linkRowText, danger && { color: colors.danger }]}>{title}</Text>
      {detail ? <Text style={styles.linkRowDetail}>{detail}</Text> : null}
      <Ionicons name="chevron-forward" size={18} color={colors.ink3} />
    </Pressable>
  );
}

// ── Forms ─────────────────────────────────────────────────────────────────────

export function Field({ label, error, hint, style, ...props }: TextInputProps & { label: string; error?: string; hint?: string }) {
  return (
    <View style={{ marginBottom: space.lg }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        placeholderTextColor={colors.ink3}
        style={[styles.input, props.multiline && styles.inputMulti, !!error && { borderColor: colors.danger }, style]}
        accessibilityLabel={label}
        {...props}
      />
      {error ? <Text style={styles.error}>{error}</Text> : hint ? <Text style={styles.hint}>{hint}</Text> : null}
    </View>
  );
}

export function Choice<T extends string>({ label, options, value, onChange, error, columns }: {
  label?: string; options: { key: T; label: string }[]; value: T | ''; onChange: (v: T) => void; error?: string; columns?: boolean;
}) {
  return (
    <View style={{ marginBottom: space.lg }}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      <View style={[styles.choices, columns && { flexDirection: 'column' }]}>
        {options.map((o) => {
          const on = o.key === value;
          return (
            <Pressable
              key={o.key}
              onPress={() => onChange(o.key)}
              accessibilityRole="radio"
              accessibilityState={{ selected: on }}
              style={[styles.choice, on && styles.choiceOn, columns && { flexBasis: 'auto' }]}
            >
              <Text style={[styles.choiceText, on && { color: colors.chalkDeep }]}>{o.label}</Text>
            </Pressable>
          );
        })}
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </View>
  );
}

export function Check({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <Pressable onPress={() => onChange(!value)} accessibilityRole="checkbox" accessibilityState={{ checked: value }} style={styles.check}>
      <Ionicons name={value ? 'checkbox' : 'square-outline'} size={26} color={value ? colors.chalk : colors.ink3} />
      <Text style={[styles.body, { flex: 1, marginLeft: 10 }]}>{label}</Text>
    </Pressable>
  );
}

// ── Status ────────────────────────────────────────────────────────────────────

export function Pill({ label, tone = 'chalk' }: { label: string; tone?: Tone }) {
  const t = TONES[tone];
  return (
    <View style={[styles.pill, { backgroundColor: t.bg }]}>
      <Text style={[styles.pillText, { color: t.fg }]}>{label}</Text>
    </View>
  );
}

const TONES: Record<Tone, { bg: string; fg: string }> = {
  chalk: { bg: colors.chalkWash, fg: colors.chalkDeep },
  pine: { bg: colors.pineWash, fg: colors.pine },
  paint: { bg: colors.paintWash, fg: colors.paintInk },
  muted: { bg: colors.ruleSoft, fg: colors.ink2 },
  danger: { bg: colors.dangerWash, fg: colors.danger },
};

export function Notice({ children, tone = 'chalk', title }: { children?: React.ReactNode; tone?: Tone; title?: string }) {
  const t = TONES[tone];
  return (
    <View style={[styles.notice, { backgroundColor: t.bg, borderLeftColor: t.fg }]}>
      {title ? <Text style={[styles.noticeTitle, { color: t.fg }]}>{title}</Text> : null}
      {/* Wrap loose text ourselves. React Native throws a fatal — not a warning —
          if a bare string reaches a View, and `{'a'} {b} {'c'}` arrives here as an
          array of strings, which the old `typeof children === 'string'` check
          missed. That crashed the quote form. */}
      {isAllText(children) ? <Text style={styles.body}>{children}</Text> : children}
    </View>
  );
}

function isAllText(children: React.ReactNode) {
  const kids = React.Children.toArray(children);
  return kids.length > 0 && kids.every((c) => typeof c === 'string' || typeof c === 'number');
}

export function ErrorText({ children }: { children?: string | null }) {
  if (!children) return null;
  return <Notice tone="danger">{children}</Notice>;
}

export function Loading() {
  return (
    <View style={styles.center}>
      <ActivityIndicator size="large" color={colors.chalk} />
    </View>
  );
}

export function Empty({ icon, title, body, children }: {
  icon: keyof typeof Ionicons.glyphMap; title: string; body?: string; children?: React.ReactNode;
}) {
  return (
    <View style={styles.empty}>
      <Ionicons name={icon} size={40} color={colors.ink3} />
      <Text style={styles.emptyTitle}>{title}</Text>
      {body ? <Text style={[styles.body, { textAlign: 'center', color: colors.ink2 }]}>{body}</Text> : null}
      {children}
    </View>
  );
}

/** Quotes so far out of the six a job can take, as six chalk-line segments. */
export function QuoteMeter({ count, max }: { count: number; max: number }) {
  return (
    <View accessible accessibilityLabel={`${count} of ${max} quotes`} style={{ flexDirection: 'row', alignItems: 'center' }}>
      <View style={{ flexDirection: 'row', gap: 3, marginRight: 8 }}>
        {Array.from({ length: max }).map((_, i) => (
          <View key={i} style={[styles.meterCell, i < count && { backgroundColor: colors.chalk, borderColor: colors.chalk }]} />
        ))}
      </View>
      <Text style={styles.meterText}>{count}/{max} quotes</Text>
    </View>
  );
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.concrete },
  screenContent: { padding: space.lg, paddingBottom: space.xxl * 2 },
  card: {
    backgroundColor: colors.paper, borderRadius: radius, padding: space.lg, marginBottom: space.md,
    borderWidth: StyleSheet.hairlineWidth, borderColor: colors.rule,
    shadowColor: colors.ink, shadowOpacity: 0.06, shadowRadius: 10, shadowOffset: { width: 0, height: 4 }, elevation: 1,
  },
  pressed: { backgroundColor: colors.slab },
  body: { fontSize: 16, lineHeight: 23, color: colors.ink },
  row: { flexDirection: 'row', paddingVertical: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.ruleSoft },
  rowLabel: { width: 118, fontSize: 14, color: colors.ink2, paddingTop: 1 },
  rowValue: { flex: 1, fontSize: 15, color: colors.ink, lineHeight: 21 },
  button: {
    minHeight: TAP, borderRadius: radius, paddingHorizontal: space.lg, flexDirection: 'row',
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, marginBottom: space.sm,
  },
  buttonSmall: { minHeight: 44, paddingHorizontal: space.md },
  buttonText: { fontSize: 17, fontWeight: '700' },
  linkRow: {
    flexDirection: 'row', alignItems: 'center', minHeight: TAP, paddingHorizontal: space.lg, gap: 12,
    backgroundColor: colors.paper, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.ruleSoft,
  },
  linkRowText: { flex: 1, fontSize: 16, color: colors.ink, fontWeight: '500' },
  linkRowDetail: { fontSize: 14, color: colors.ink3 },
  fieldLabel: { fontSize: 15, fontWeight: '600', color: colors.ink, marginBottom: 6 },
  input: {
    minHeight: TAP, backgroundColor: colors.paper, borderWidth: 1, borderColor: colors.rule, borderRadius: radius,
    paddingHorizontal: 14, fontSize: 17, color: colors.ink,
  },
  inputMulti: { minHeight: 120, paddingTop: 14, textAlignVertical: 'top' },
  error: { color: colors.danger, fontSize: 14, marginTop: 6 },
  hint: { color: colors.ink2, fontSize: 13.5, marginTop: 6, lineHeight: 19 },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  choice: {
    minHeight: 48, paddingHorizontal: 14, justifyContent: 'center', borderRadius: radius, borderWidth: 1,
    borderColor: colors.rule, backgroundColor: colors.paper, flexGrow: 1, flexBasis: '30%',
  },
  choiceOn: { borderColor: colors.chalk, borderWidth: 2, backgroundColor: colors.chalkWash },
  choiceText: { fontSize: 15, fontWeight: '600', color: colors.ink, textAlign: 'center' },
  check: { flexDirection: 'row', alignItems: 'center', minHeight: 48, marginBottom: space.md },
  pill: { alignSelf: 'flex-start', paddingHorizontal: 9, paddingVertical: 4, borderRadius: 999 },
  pillText: { fontSize: 12.5, fontWeight: '700', letterSpacing: 0.3 },
  notice: { borderLeftWidth: 4, borderRadius: 6, padding: space.md, marginBottom: space.md },
  noticeTitle: { fontWeight: '700', fontSize: 15, marginBottom: 4 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.concrete, padding: space.xl },
  empty: { alignItems: 'center', padding: space.xl, gap: 8, marginTop: space.xl },
  emptyTitle: { fontSize: 19, fontWeight: '700', color: colors.ink, textAlign: 'center' },
  meterCell: { width: 14, height: 8, borderRadius: 2, borderWidth: 1, borderColor: colors.rule, backgroundColor: colors.slab },
  meterText: { fontSize: 13.5, color: colors.ink2, fontVariant: ['tabular-nums'] },
});
