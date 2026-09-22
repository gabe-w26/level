import React, { useEffect, useState } from 'react';
import { Alert, Pressable, Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { api, Option } from '../../lib/api';
import { Button, ErrorText, Field, Loading, Screen } from '../../components/ui';
import { colors, space } from '../../lib/theme';

export default function Review() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [parts, setParts] = useState<Option[] | null>(null);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [body, setBody] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.reviewParts().then((r) => setParts(r.parts)).catch((e) => setError(e.message));
  }, []);

  async function submit() {
    if (parts && parts.some((p) => !scores[p.key])) {
      setError('Choose 1 to 5 stars for each part.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await api.review(Number(id), scores, body.trim());
      Alert.alert('Thanks', res.message);
      router.back();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!parts && !error) return <Loading />;

  return (
    <Screen keyboard>
      <Text style={{ fontSize: 16, color: colors.ink2, marginBottom: space.lg }}>
        Your review appears on the trade’s profile. Reviews never change who gets offered jobs — they help other Kiwis choose.
      </Text>
      <ErrorText>{error}</ErrorText>
      {(parts || []).map((p) => (
        <View key={p.key} style={{ marginBottom: space.lg }}>
          <Text style={{ fontSize: 16, fontWeight: '600', color: colors.ink, marginBottom: 6 }}>{p.label}</Text>
          <View style={{ flexDirection: 'row', gap: 6 }}>
            {[1, 2, 3, 4, 5].map((n) => (
              <Pressable key={n} onPress={() => setScores((s) => ({ ...s, [p.key]: n }))} hitSlop={4}
                accessibilityRole="button" accessibilityLabel={`${p.label}: ${n} star${n === 1 ? '' : 's'}`}
                style={{ width: 48, height: 48, alignItems: 'center', justifyContent: 'center' }}>
                <Ionicons name={(scores[p.key] || 0) >= n ? 'star' : 'star-outline'} size={34}
                  color={(scores[p.key] || 0) >= n ? colors.paint : colors.ink3} />
              </Pressable>
            ))}
          </View>
        </View>
      ))}
      <Field label="Anything else? (optional)" value={body} onChangeText={setBody} multiline
        placeholder="What went well, what could be better." />
      <Button title="Post review" onPress={submit} loading={busy} />
    </Screen>
  );
}
