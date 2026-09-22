import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { Tabs, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../lib/auth';
import { colors } from '../lib/theme';

export interface TabDef {
  name: string;
  title: string;
  icon: keyof typeof Ionicons.glyphMap;
  badge?: number;
}

function Bell() {
  const router = useRouter();
  const { counts } = useAuth();
  return (
    <Pressable onPress={() => router.push('/notifications')} hitSlop={12} accessibilityRole="button"
      accessibilityLabel={`Notifications${counts.notifications ? `, ${counts.notifications} unread` : ''}`}
      style={{ paddingHorizontal: 16, paddingVertical: 6 }}>
      <Ionicons name="notifications-outline" size={25} color={colors.ink} />
      {counts.notifications ? (
        <View style={{ position: 'absolute', top: 0, right: 8, backgroundColor: colors.paint, borderRadius: 10,
          minWidth: 19, height: 19, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4 }}>
          <Text style={{ color: '#fff', fontSize: 11, fontWeight: '800' }}>{counts.notifications > 99 ? '99+' : counts.notifications}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

/** The bottom tab bar both kinds of account use. */
export function TabsShell({ tabs }: { tabs: TabDef[] }) {
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.paper },
        headerTitleStyle: { color: colors.ink, fontWeight: '800', fontSize: 19 },
        headerRight: () => <Bell />,
        tabBarActiveTintColor: colors.chalk,
        tabBarInactiveTintColor: colors.ink3,
        tabBarStyle: { backgroundColor: colors.paper, borderTopColor: colors.rule, minHeight: 60 },
        tabBarLabelStyle: { fontSize: 12, fontWeight: '600' },
        tabBarBadgeStyle: { backgroundColor: colors.paint, fontSize: 11 },
        sceneStyle: { backgroundColor: colors.concrete },
      }}
    >
      {tabs.map((t) => (
        <Tabs.Screen
          key={t.name}
          name={t.name}
          options={{
            title: t.title,
            tabBarBadge: t.badge ? t.badge : undefined,
            tabBarIcon: ({ color, size }) => <Ionicons name={t.icon} size={size} color={color} />,
          }}
        />
      ))}
    </Tabs>
  );
}
