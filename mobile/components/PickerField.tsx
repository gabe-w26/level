import React, { useMemo, useState } from 'react';
import { Modal, Pressable, SectionList, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { colors, radius, space, TAP } from '../lib/theme';

export interface PickerSection { title?: string; data: { key: string; label: string }[] }

/** A form field that opens a full-screen, searchable list — for trades and areas. */
export function PickerField({ label, placeholder, sections, value, onChange, error }: {
  label: string; placeholder: string; sections: PickerSection[]; value: string; onChange: (key: string) => void; error?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const selected = useMemo(() => sections.flatMap((s) => s.data).find((o) => o.key === value), [sections, value]);
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sections;
    return sections
      .map((s) => ({ ...s, data: s.data.filter((o) => o.label.toLowerCase().includes(q) || (s.title || '').toLowerCase().includes(q)) }))
      .filter((s) => s.data.length);
  }, [sections, search]);

  return (
    <View style={{ marginBottom: space.lg }}>
      <Text style={styles.label}>{label}</Text>
      <Pressable onPress={() => setOpen(true)} accessibilityRole="button" accessibilityLabel={`${label}: ${selected?.label || placeholder}`}
        style={[styles.field, !!error && { borderColor: colors.danger }]}>
        <Text style={[styles.value, !selected && { color: colors.ink3 }]} numberOfLines={1}>{selected?.label || placeholder}</Text>
        <Ionicons name="chevron-down" size={20} color={colors.ink2} />
      </Pressable>
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Modal visible={open} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setOpen(false)}>
        <SafeAreaView style={{ flex: 1, backgroundColor: colors.concrete }} edges={['top', 'bottom']}>
          <View style={styles.sheetHead}>
            <Text style={styles.sheetTitle}>{label}</Text>
            <Pressable onPress={() => setOpen(false)} hitSlop={12} accessibilityRole="button" accessibilityLabel="Close">
              <Text style={styles.done}>Close</Text>
            </Pressable>
          </View>
          <View style={styles.searchWrap}>
            <Ionicons name="search" size={18} color={colors.ink3} />
            <TextInput value={search} onChangeText={setSearch} placeholder="Search" placeholderTextColor={colors.ink3}
              style={styles.search} autoCorrect={false} clearButtonMode="while-editing" />
          </View>
          <SectionList
            sections={filtered}
            keyExtractor={(o) => o.key}
            keyboardShouldPersistTaps="handled"
            stickySectionHeadersEnabled
            renderSectionHeader={({ section }) => section.title ? <Text style={styles.section}>{section.title}</Text> : null}
            renderItem={({ item }) => (
              <Pressable onPress={() => { onChange(item.key); setOpen(false); setSearch(''); }}
                style={({ pressed }) => [styles.option, pressed && { backgroundColor: colors.slab }]} accessibilityRole="button">
                <Text style={[styles.optionText, item.key === value && { color: colors.chalkDeep, fontWeight: '700' }]}>{item.label}</Text>
                {item.key === value ? <Ionicons name="checkmark" size={22} color={colors.chalk} /> : null}
              </Pressable>
            )}
          />
        </SafeAreaView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  label: { fontSize: 15, fontWeight: '600', color: colors.ink, marginBottom: 6 },
  field: {
    minHeight: TAP, backgroundColor: colors.paper, borderWidth: 1, borderColor: colors.rule, borderRadius: radius,
    paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  value: { fontSize: 17, color: colors.ink, flex: 1 },
  error: { color: colors.danger, fontSize: 14, marginTop: 6 },
  sheetHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: space.lg },
  sheetTitle: { fontSize: 20, fontWeight: '800', color: colors.ink },
  done: { fontSize: 17, color: colors.chalkDeep, fontWeight: '600' },
  searchWrap: {
    flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: space.lg, marginBottom: space.sm,
    backgroundColor: colors.paper, borderRadius: radius, paddingHorizontal: 12, borderWidth: 1, borderColor: colors.rule,
  },
  search: { flex: 1, minHeight: 46, fontSize: 17, color: colors.ink },
  section: { backgroundColor: colors.concrete, paddingHorizontal: space.lg, paddingTop: 14, paddingBottom: 6, fontSize: 12,
    fontWeight: '700', letterSpacing: 0.8, color: colors.ink2, textTransform: 'uppercase' },
  option: {
    minHeight: TAP, paddingHorizontal: space.lg, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: colors.paper, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.ruleSoft,
  },
  optionText: { fontSize: 17, color: colors.ink },
});
