import React, { useMemo, useState } from 'react';
import { Alert, Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { api, JobInput, PhotoInput } from '../../lib/api';
import { pickPhotos } from '../../lib/photos';
import { useAppConfig } from '../../lib/auth';
import { Button, Choice, ErrorText, Field, Loading, Notice, Screen } from '../../components/ui';
import { PickerField } from '../../components/PickerField';
import { colors, radius, space } from '../../lib/theme';

const BLANK: JobInput = { category: '', area: '', suburb: '', address: '', title: '', description: '', value_band: '', timing: '', property_type: '' };

export default function PostJob() {
  const router = useRouter();
  const config = useAppConfig();
  const [f, setF] = useState<JobInput>(BLANK);
  const [photos, setPhotos] = useState<PhotoInput[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof JobInput) => (v: string) => setF((old) => ({ ...old, [k]: v }));

  const categories = useMemo(() => [{ data: (config?.categories || []).map((c) => ({ key: c.slug, label: c.name })) }], [config]);
  const areas = useMemo(() => (config?.regions || []).map((r) => ({ title: r.region, data: r.areas.map((a) => ({ key: a.slug, label: a.name })) })), [config]);
  const licenceNote = config?.categories.find((c) => c.slug === f.category)?.licence_note;
  const maxPhotos = config?.max_photos || 6;

  if (!config) return <Loading />;

  async function addPhoto(source: 'camera' | 'library') {
    const added = await pickPhotos(source, maxPhotos - photos.length);
    if (added.length) setPhotos((old) => [...old, ...added].slice(0, maxPhotos));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      const res = await api.postJob(f, photos);
      setF(BLANK);
      setPhotos([]);
      if (res.held) {
        router.push({ pathname: '/verify-phone', params: { job: String(res.job_id) } });
      } else {
        Alert.alert('Job posted', res.message);
        router.push(`/job/${res.job_id}`);
      }
    } catch (e: any) {
      setErrors(e.errors || {});
      setError(Object.keys(e.errors || {}).length ? 'Check the highlighted fields.' : e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen keyboard>
      <Text style={styles.intro}>
        Up to {config.trades_per_job} local trades see your job. You’ll get up to {config.max_quotes} quotes, and trades see your suburb — never your address — until you choose them.
      </Text>
      <ErrorText>{error}</ErrorText>
      <PickerField label="What kind of trade?" placeholder="Choose a trade" sections={categories} value={f.category}
        onChange={set('category')} error={errors.category} />
      {licenceNote ? <Notice tone="chalk">{licenceNote}</Notice> : null}
      <PickerField label="Where is the job?" placeholder="Choose an area" sections={areas} value={f.area}
        onChange={set('area')} error={errors.area} />
      <Field label="Suburb" value={f.suburb} onChangeText={set('suburb')} error={errors.suburb} placeholder="e.g. Ponsonby"
        hint="Trades see this, not your address." />
      <Field label="Job title" value={f.title} onChangeText={set('title')} error={errors.title} maxLength={80}
        placeholder="e.g. Replace rotten deck boards" />
      <Field label="Describe the job" value={f.description} onChangeText={set('description')} error={errors.description} multiline
        placeholder="What needs doing, sizes, materials, access — the more detail, the better the quotes."
        hint="A few sentences, at least 30 characters." />
      <Choice label="Rough budget" options={config.value_bands.map((b) => ({ key: b.key, label: b.label }))}
        value={f.value_band} onChange={set('value_band')} error={errors.value_band} columns />
      <Choice label="When do you want it done?" options={config.timing} value={f.timing} onChange={set('timing')}
        error={errors.timing} columns />
      <Choice label="Type of property" options={config.property_types} value={f.property_type} onChange={set('property_type')}
        error={errors.property_type} />
      <Field label="Street address (optional)" value={f.address} onChangeText={set('address')} textContentType="fullStreetAddress"
        hint="Only shared with a trade once you share your contact details or accept their quote." />

      <Text style={styles.label}>Photos (optional)</Text>
      <View style={styles.photos}>
        {photos.map((p, i) => (
          <View key={p.uri} style={styles.photoWrap}>
            <Image source={{ uri: p.uri }} style={styles.photo} accessibilityLabel={`Photo ${i + 1}`} />
            <Pressable onPress={() => setPhotos((old) => old.filter((x) => x.uri !== p.uri))} style={styles.remove}
              accessibilityRole="button" accessibilityLabel={`Remove photo ${i + 1}`} hitSlop={8}>
              <Ionicons name="close" size={16} color="#fff" />
            </Pressable>
          </View>
        ))}
      </View>
      {errors.photos ? <Text style={{ color: colors.danger, marginBottom: 8 }}>{errors.photos}</Text> : null}
      <View style={{ flexDirection: 'row', gap: 10, marginBottom: space.lg }}>
        <Button title="Take photo" icon="camera-outline" variant="quiet" small onPress={() => addPhoto('camera')} style={{ flex: 1 }} />
        <Button title="From library" icon="images-outline" variant="quiet" small onPress={() => addPhoto('library')} style={{ flex: 1 }} />
      </View>

      <Button title="Post job" icon="send" onPress={submit} loading={busy} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  intro: { fontSize: 15.5, lineHeight: 22, color: colors.ink2, marginBottom: space.lg },
  label: { fontSize: 15, fontWeight: '600', color: colors.ink, marginBottom: 8 },
  photos: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 10 },
  photoWrap: { width: 96, height: 96 },
  photo: { width: 96, height: 96, borderRadius: radius, backgroundColor: colors.ruleSoft },
  remove: { position: 'absolute', top: 4, right: 4, width: 26, height: 26, borderRadius: 13, backgroundColor: 'rgba(22,32,43,.75)',
    alignItems: 'center', justifyContent: 'center' },
});
