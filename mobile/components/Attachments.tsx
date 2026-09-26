/**
 * Files inside a message, on a phone.
 *
 * Two things this has to get right that the website gets for free:
 *
 *  · **The token travels with the image.** A message attachment is not in the
 *    public uploads folder — it sits behind an authenticated route, because a
 *    photo of somebody's back door is between the two people on that job. So an
 *    <Image> here has to carry the bearer token in its own headers, and until
 *    they've loaded there's a placeholder rather than a blank gap.
 *
 *  · **A document is not a photo.** A PDF or a spreadsheet gets a row you can
 *    tap to open, not a thumbnail of nothing.
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Image, Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { MessageFile, fileHeaders } from '../lib/api';
import { colors, radius } from '../lib/theme';

export function Attachments({ files, mine }: { files: MessageFile[]; mine: boolean }) {
  const [headers, setHeaders] = useState<Record<string, string> | null>(null);

  useEffect(() => {
    let alive = true;
    fileHeaders().then((h) => { if (alive) setHeaders(h); });
    return () => { alive = false; };
  }, []);

  if (!files.length) return null;
  const faint = mine ? 'rgba(255,255,255,.75)' : colors.ink3;

  return (
    <View style={{ marginTop: 6, gap: 6 }}>
      {files.map((f) => (f.kind === 'photo' ? (
        <Photo key={f.id} file={f} headers={headers} />
      ) : (
        <Pressable
          key={f.id}
          onPress={() => openFile(f, headers)}
          accessibilityRole="button"
          accessibilityLabel={`Open ${f.name}`}
          style={[styles.doc, mine && { backgroundColor: 'rgba(255,255,255,.15)' }]}>
          <Ionicons name="document-text-outline" size={18} color={mine ? '#fff' : colors.ink2} />
          <View style={{ flex: 1 }}>
            <Text numberOfLines={1} style={[styles.docName, mine && { color: '#fff' }]}>{f.name}</Text>
            {f.size ? <Text style={[styles.docSize, { color: faint }]}>{f.size}</Text> : null}
          </View>
          <Ionicons name="open-outline" size={16} color={faint} />
        </Pressable>
      )))}
    </View>
  );
}

function Photo({ file, headers }: { file: MessageFile; headers: Record<string, string> | null }) {
  const [failed, setFailed] = useState(false);

  // No headers yet means we haven't read the token out of secure storage — asking
  // for the image now would come back 401 and cache the failure.
  if (!headers) {
    return (
      <View style={[styles.photo, styles.placeholder]}>
        <ActivityIndicator color={colors.ink3} />
      </View>
    );
  }
  if (failed) {
    return (
      <View style={[styles.photo, styles.placeholder]}>
        <Ionicons name="image-outline" size={22} color={colors.ink3} />
        <Text style={styles.failed}>Couldn’t load this photo</Text>
      </View>
    );
  }
  return (
    <Image
      source={{ uri: file.url, headers }}
      onError={() => setFailed(true)}
      accessibilityLabel={file.name || 'Photo'}
      style={styles.photo}
      resizeMode="cover"
    />
  );
}

async function openFile(file: MessageFile, headers: Record<string, string> | null) {
  // Linking can't send a header, so a document opens in the browser where the
  // session cookie does the same job the token does here. If that isn't possible
  // the name and size are still on screen, which is better than a dead end.
  try {
    await Linking.openURL(file.url);
  } catch {
    /* nothing useful to say — the row stays as it was */
  }
}

const styles = StyleSheet.create({
  photo: { width: 210, height: 158, borderRadius: 10, backgroundColor: colors.slab },
  placeholder: { alignItems: 'center', justifyContent: 'center', gap: 6 },
  failed: { fontSize: 12, color: colors.ink3 },
  doc: {
    flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: colors.slab,
    borderRadius: radius, paddingHorizontal: 10, paddingVertical: 9,
  },
  docName: { fontSize: 14, fontWeight: '600', color: colors.ink },
  docSize: { fontSize: 11.5, marginTop: 1 },
});
