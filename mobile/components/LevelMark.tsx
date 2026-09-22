import React from 'react';
import { View } from 'react-native';
import { colors } from '../lib/theme';

/** The spirit-level mark from the app icon, drawn with plain views (no SVG dependency). */
export function LevelMark({ size = 40 }: { size?: number }) {
  const w = size * 1.9;
  const h = size;
  const stroke = Math.max(2, size * 0.09);
  return (
    <View style={{ width: w, height: h, borderRadius: h * 0.22, borderWidth: stroke, borderColor: colors.chalk,
      alignItems: 'center', justifyContent: 'center' }}>
      <View style={{ width: w * 0.46, height: h * 0.44, borderRadius: h * 0.22, backgroundColor: colors.chalk,
        alignItems: 'center', justifyContent: 'center' }}>
        <View style={{ width: h * 0.24, height: h * 0.24, borderRadius: h, backgroundColor: colors.concrete }} />
      </View>
    </View>
  );
}
