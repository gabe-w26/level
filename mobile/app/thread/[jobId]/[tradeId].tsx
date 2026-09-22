import React, { useEffect, useRef, useState } from 'react';
import { FlatList, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useLocalSearchParams, useNavigation } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api, Message } from '../../../lib/api';
import { useAuth } from '../../../lib/auth';
import { useLoad } from '../../../lib/useLoad';
import { when } from '../../../lib/format';
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
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const list = useRef<FlatList<Message>>(null);

  useEffect(() => {
    if (data) navigation.setOptions({ title: data.with || 'Messages' });
  }, [data?.with, navigation]);            // eslint-disable-line react-hooks/exhaustive-deps

  async function send() {
    const text = body.trim();
    if (!text) return;
    setSending(true);
    setSendError(null);
    try {
      await api.sendMessage(Number(jobId), Number(tradeId), text);
      setBody('');
      await reload();
      setTimeout(() => list.current?.scrollToEnd({ animated: true }), 100);
    } catch (e: any) {
      setSendError(e.message);
    } finally {
      setSending(false);
    }
  }

  if (!data && !error) return <Loading />;

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
        renderItem={({ item }) => (
          <View style={[styles.bubble, item.mine ? styles.mine : styles.theirs]}>
            <Text style={[styles.text, item.mine && { color: '#fff' }]} selectable>{item.body}</Text>
            <Text style={[styles.time, item.mine && { color: 'rgba(255,255,255,.75)' }]}>{when(item.created_at)}</Text>
          </View>
        )}
      />
      <View style={[styles.composer, { paddingBottom: Math.max(insets.bottom, 10) }]}>
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
        <Pressable onPress={send} disabled={sending || !body.trim()} accessibilityRole="button" accessibilityLabel="Send"
          style={[styles.send, (sending || !body.trim()) && { opacity: 0.4 }]}>
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
  composer: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 8, paddingHorizontal: space.md, paddingTop: 10,
    backgroundColor: colors.paper, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.rule,
  },
  input: {
    flex: 1, minHeight: 46, maxHeight: 140, backgroundColor: colors.slab, borderRadius: radius, paddingHorizontal: 14,
    paddingTop: 12, paddingBottom: 12, fontSize: 16, color: colors.ink,
  },
  send: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.chalk, alignItems: 'center', justifyContent: 'center' },
});
