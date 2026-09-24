import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../lib/auth';
import { Button, Loading, Notice } from '../components/ui';
import { LevelMark } from '../components/LevelMark';
import { colors, radius, space } from '../lib/theme';

const RULES: { icon: keyof typeof Ionicons.glyphMap; title: string; body: string }[] = [
  { icon: 'people-outline', title: '15 trades per job', body: 'Your job goes to 15 local trades who do that work.' },
  { icon: 'time-outline', title: '4 hours to quote', body: 'Trades who don’t quote in time pass the job on to someone new.' },
  { icon: 'albums-outline', title: '3 quotes, then it closes', body: 'First in, first served. You’re never flooded with calls.' },
  { icon: 'scale-outline', title: 'Fair for tradies', body: 'Jobs are shared out evenly. No lead fees, no bidding for work.' },
];

/** The first screen. Nothing here needs an account. */
export default function Welcome() {
  const router = useRouter();
  const { isLoading, token, user, refresh } = useAuth();

  if (isLoading || (token && user)) return <Loading />;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.concrete }} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.brandRow}>
          <LevelMark size={44} />
          <Text style={styles.brand}>Level</Text>
        </View>
        <Text style={styles.headline}>Fair work for NZ trades.</Text>
        <Text style={styles.lede}>
          Post a job and get quotes from local tradies — or, if you’re a tradie, get jobs without paying per lead.
        </Text>

        {token && !user ? (
          <Notice tone="paint" title="Can’t reach Level">
            <Text style={{ fontSize: 15, marginBottom: 8 }}>You’re signed in, but we couldn’t connect. Check your signal and try again.</Text>
            <Button title="Try again" small onPress={refresh} />
          </Notice>
        ) : null}

        <Pressable style={({ pressed }) => [styles.pick, pressed && { opacity: 0.85 }]} accessibilityRole="button"
          onPress={() => router.push({ pathname: '/signup', params: { role: 'customer' } })}>
          <View style={[styles.pickIcon, { backgroundColor: colors.chalk }]}>
            <Ionicons name="home-outline" size={28} color="#fff" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.pickTitle}>I need a tradie</Text>
            <Text style={styles.pickBody}>Post a job for free and compare quotes.</Text>
          </View>
          <Ionicons name="chevron-forward" size={22} color={colors.ink3} />
        </Pressable>

        <Pressable style={({ pressed }) => [styles.pick, pressed && { opacity: 0.85 }]} accessibilityRole="button"
          onPress={() => router.push({ pathname: '/signup', params: { role: 'trade' } })}>
          <View style={[styles.pickIcon, { backgroundColor: colors.ink }]}>
            <Ionicons name="hammer-outline" size={28} color="#fff" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.pickTitle}>I’m a tradie</Text>
            <Text style={styles.pickBody}>Get local jobs with a fair go at every one.</Text>
          </View>
          <Ionicons name="chevron-forward" size={22} color={colors.ink3} />
        </Pressable>

        <Button title="I already have an account — log in" variant="quiet" onPress={() => router.push('/login')} style={{ marginTop: space.sm }} />

        <Text style={styles.section}>HOW LEVEL WORKS</Text>
        {RULES.map((r) => (
          <View key={r.title} style={styles.rule}>
            <Ionicons name={r.icon} size={24} color={colors.chalk} />
            <View style={{ flex: 1 }}>
              <Text style={styles.ruleTitle}>{r.title}</Text>
              <Text style={styles.ruleBody}>{r.body}</Text>
            </View>
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  content: { padding: space.xl, paddingBottom: space.xxl * 2 },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: space.lg },
  brand: { fontSize: 34, fontWeight: '900', color: colors.ink, letterSpacing: -1 },
  headline: { fontSize: 36, lineHeight: 40, fontWeight: '900', color: colors.ink, letterSpacing: -1, marginTop: space.xl },
  lede: { fontSize: 17, lineHeight: 25, color: colors.ink2, marginTop: space.md, marginBottom: space.xl },
  pick: {
    flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: colors.paper, borderRadius: radius,
    padding: space.lg, marginBottom: space.md, borderWidth: 1, borderColor: colors.rule, minHeight: 88,
  },
  pickIcon: { width: 52, height: 52, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  pickTitle: { fontSize: 19, fontWeight: '800', color: colors.ink },
  pickBody: { fontSize: 15, color: colors.ink2, marginTop: 2 },
  section: { fontSize: 12, fontWeight: '700', letterSpacing: 1, color: colors.ink2, marginTop: space.xxl, marginBottom: space.md },
  rule: { flexDirection: 'row', gap: 14, marginBottom: space.lg },
  ruleTitle: { fontSize: 16, fontWeight: '700', color: colors.ink },
  ruleBody: { fontSize: 15, color: colors.ink2, lineHeight: 21, marginTop: 2 },
});
