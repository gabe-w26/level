import React, { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../lib/theme';
import { countdown } from '../lib/format';

/**
 * Time left to quote, ticking live. `deadline` is a local timestamp worked out
 * from the server's seconds_left when the data arrived, so a phone with the
 * wrong clock still counts down correctly. Turns marking-paint orange in the
 * last three hours.
 */
export function Countdown({ deadline, big }: { deadline: number; big?: boolean }) {
  const [now, setNow] = useState(Date.now());
  const left = Math.max(0, Math.round((deadline - now) / 1000));

  useEffect(() => {
    if (left <= 0) return;
    const t = setInterval(() => setNow(Date.now()), left < 3600 ? 1000 : 15000);
    return () => clearInterval(t);
  }, [left < 3600, left <= 0]);        // eslint-disable-line react-hooks/exhaustive-deps

  const urgent = left < 3 * 3600;
  const color = left <= 0 ? colors.ink3 : urgent ? colors.paintInk : colors.ink;
  return (
    <View style={[styles.wrap, urgent && left > 0 && styles.urgent]} accessibilityLabel={countdown(left)}>
      <Ionicons name="time-outline" size={big ? 20 : 16} color={color} />
      <Text style={[styles.text, { color }, big && { fontSize: 18 }]}>{countdown(left)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: 'row', alignItems: 'center', gap: 5, alignSelf: 'flex-start', paddingVertical: 3, paddingHorizontal: 8,
    borderRadius: 999, backgroundColor: colors.slab },
  urgent: { backgroundColor: colors.paintWash },
  text: { fontSize: 14.5, fontWeight: '700', fontVariant: ['tabular-nums'] },
});
