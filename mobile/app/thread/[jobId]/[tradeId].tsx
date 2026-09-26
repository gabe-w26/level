import React, { useEffect, useRef, useState } from 'react';
import { FlatList, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useLocalSearchParams, useNavigation } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api, AskInput, Message, PhotoInput } from '../../../lib/api';
import { useAuth } from '../../../lib/auth';
import { useLoad } from '../../../lib/useLoad';
import { when } from '../../../lib/format';
import { pickPhotos } from '../../../lib/photos';
import { Attachments } from '../../../components/Attachments';
import { AskForm, ExtraWorkList } from '../../../components/ExtraWork';
import { ErrorText, Loading } from '../../../components/ui';
import { colors, radius, space } from '../../../lib/theme';

export default function Thread() {
  const { jobId, tradeId } = useLocalSearchParams<{ jobId: string; tradeId: string }>();
  const navigation = useNavigation();
  const insets = useSafeAreaInsets();
  const { refresh: refreshCounts } = useAuth();
  const { data, error, reload } = useLoad(async () => {
    const res = await api.thread(Number(jobId), Number(tradeId));
    refreshCounts();                     // opening the thread marks it read
    return res;
  }, 10000);
  const [body, setBody] = useState('');
  const [files, setFiles] = useState<PhotoInput[]>([]);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [askBusy, setAskBusy] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const list = useRef<FlatList<Message>>(null);

  const maxFiles = data?.max_files ?? 6;

  useEffect(() => {
    if (data) navigation.setOptions({ title: data.with || 'Messages' });
  }, [data?.with, navigation]);            // eslint-disable-line react-hooks/exhaustive-deps

  async function addPhotos(source: 'camera' | 'library') {
    const picked = await pickPhotos(source, maxFiles - files.length);
    if (picked.length) setFiles((was) => [...was, ...picked]);
  }

  async function send() {
    const text = body.trim();
    if (!text && !files.length) return;     // the server says the same thing
    setSending(true);
    setSendError(null);
    try {
      await api.sendMessage(Number(jobId), Number(tradeId), text, files);
      setBody('');
      setFiles([]);
      await reload();
      setTimeout(() => list.current?.scrollToEnd({ animated: true }), 100);
    } catch (e: any) {
      setSendError(e.message);
    } finally {
      setSending(false);
    }
  }

  async function ask(fields: AskInput) {
    setAskBusy(true);
    setAskError(null);
    try {
      await api.askForExtra(Number(jobId), Number(tradeId), fields);
      setAsking(false);
      await reload();
    } catch (e: any) {
      setAskError(e.message);
    } finally {
      setAskBusy(false);
    }
  }

  async function answer(id: number, decision: 'accepted' | 'declined' | 'withdrawn') {
    setAskBusy(true);
    setAskError(null);
    try {
      await api.answerExtra(Number(jobId), Number(tradeId), id, decision);
      await reload();
    } catch (e: any) {
      setAskError(e.message);
    } finally {
      setAskBusy(false);
    }
  }

  if (!data && !error) return <Loading />;

  const canSend = !sending && (body.trim().length > 0 || files.length > 0);

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: colors.concrete }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={insets.top + 44}>
      {data ? <Text style={styles.jobTitle} numberOfLines={1}>{data.job.title}</Text> : null}
      <View style={{ paddingHorizontal: space.lg }}><ErrorText>{error || sendError}</ErrorText></View>
      <FlatList
        ref={list}
        data={data?.messages || []}
        keyExtractor={(m) => String(m.id)}
        contentContainerStyle={{ padding: space.lg, paddingBottom: space.md, flexGrow: 1 }}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: false })}
        ListEmptyComponent={<Text style={styles.empty}>No messages yet. Ask a question about the job or the quote.</Text>}
        ListFooterComponent={
          /* Extra work sits under the conversation rather than in it: these are
             the things somebody has to come back to and answer, and they'd be
             scrolled past as bubbles — which is the whole problem this solves. */
          <View style={{ gap: 10, marginTop: data?.asks?.length || asking ? space.lg : 0 }}>
            <ErrorText>{askError}</ErrorText>
            {data ? (
              <ExtraWorkList asks={data.asks} extra={data.extra} busy={askBusy} onAnswer={answer} />
            ) : null}
            {asking ? (
              <AskForm busy={askBusy} error={askError}
                onSubmit={ask} onCancel={() => { setAsking(false); setAskError(null); }} />
            ) : (
              <Pressable onPress={() => setAsking(true)} accessibilityRole="button"
                style={styles.askLink}>
                <Ionicons name="add-circle-outline" size={18} color={colors.ink2} />
                <Text style={styles.askLinkText}>Ask about extra work</Text>
              </Pressable>
            )}
          </View>
        }
        renderItem={({ item }) => (
          <View style={[styles.bubble, item.mine ? styles.mine : styles.theirs]}>
            <Text style={[styles.text, item.mine && { color: '#fff' }]} selectable>{item.body}</Text>
            <Attachments files={item.files || []} mine={item.mine} />
            <Text style={[styles.time, item.mine && { color: 'rgba(255,255,255,.75)' }]}>{when(item.created_at)}</Text>
          </View>
        )}
      />

      {files.length ? (
        <View style={styles.tray}>
          {files.map((f, i) => (
            <Pressable key={`${f.name}-${i}`} onPress={() => setFiles(files.filter((_, n) => n !== i))}
              accessibilityRole="button" accessibilityLabel={`Remove ${f.name}`} style={styles.chip}>
              <Ionicons name="image" size={14} color={colors.ink2} />
              <Text style={styles.chipText} numberOfLines={1}>{f.name}</Text>
              <Ionicons name="close" size={14} color={colors.ink3} />
            </Pressable>
          ))}
          <Text style={styles.trayHint}>
            {files.length} of {maxFiles} — tap one to remove it
          </Text>
        </View>
      ) : null}

      <View style={[styles.composer, { paddingBottom: Math.max(insets.bottom, 10) }]}>
        <Pressable onPress={() => addPhotos('library')} onLongPress={() => addPhotos('camera')}
          disabled={sending || files.length >= maxFiles}
          accessibilityRole="button" accessibilityLabel="Add a photo"
          accessibilityHint="Long press to take one with the camera"
          style={[styles.attach, (sending || files.length >= maxFiles) && { opacity: 0.4 }]}>
          <Ionicons name="camera-outline" size={22} color={colors.ink2} />
        </Pressable>
        <TextInput
          value={body}
          onChangeText={setBody}
          placeholder="Write a message"
          placeholderTextColor={colors.ink3}
          multiline
          maxLength={4000}
          style={styles.input}
          accessibilityLabel="Message"
        />
        <Pressable onPress={send} disabled={!canSend} accessibilityRole="button" accessibilityLabel="Send"
          style={[styles.send, !canSend && { opacity: 0.4 }]}>
          <Ionicons name="send" size={20} color="#fff" />
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  jobTitle: { fontSize: 14, color: colors.ink2, paddingHorizontal: space.lg, paddingTop: space.md, fontWeight: '600' },
  empty: { textAlign: 'center', color: colors.ink2, marginTop: space.xxl, fontSize: 15, paddingHorizontal: space.xl },
  bubble: { maxWidth: '82%', borderRadius: 16, paddingHorizontal: 14, paddingVertical: 10, marginBottom: 8 },
  mine: { alignSelf: 'flex-end', backgroundColor: colors.chalk, borderBottomRightRadius: 4 },
  theirs: { alignSelf: 'flex-start', backgroundColor: colors.paper, borderBottomLeftRadius: 4, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.rule },
  text: { fontSize: 16, lineHeight: 22, color: colors.ink },
  time: { fontSize: 11.5, color: colors.ink3, marginTop: 4 },
  askLink: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 12 },
  askLinkText: { fontSize: 15, fontWeight: '600', color: colors.ink2 },
  tray: {
    flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6,
    paddingHorizontal: space.md, paddingTop: 10, backgroundColor: colors.paper,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.rule,
  },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 5, maxWidth: 170,
    backgroundColor: colors.slab, borderRadius: 14, paddingHorizontal: 9, paddingVertical: 6,
  },
  chipText: { flexShrink: 1, fontSize: 12.5, color: colors.ink2 },
  trayHint: { flexBasis: '100%', fontSize: 11.5, color: colors.ink3, marginTop: 2 },
  composer: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 8, paddingHorizontal: space.md, paddingTop: 10,
    backgroundColor: colors.paper, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.rule,
  },
  attach: { width: 42, height: 46, alignItems: 'center', justifyContent: 'center' },
  input: {
    flex: 1, minHeight: 46, maxHeight: 140, backgroundColor: colors.slab, borderRadius: radius, paddingHorizontal: 14,
    paddingTop: 12, paddingBottom: 12, fontSize: 16, color: colors.ink,
  },
  send: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.chalk, alignItems: 'center', justifyContent: 'center' },
});
