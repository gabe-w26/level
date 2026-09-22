import React, { useState } from 'react';
import { Alert, Linking, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import Constants from 'expo-constants';
import { api, TradeProfile, TradeStatus } from '../lib/api';
import { useAuth, useAppConfig } from '../lib/auth';
import { SITE_URL } from '../lib/config';
import { useLoad } from '../lib/useLoad';
import { day } from '../lib/format';
import { Button, Card, Label, LinkRow, Pill, Row, Screen, Title } from './ui';
import { SetupNotice } from './SetupNotice';
import { colors, space } from '../lib/theme';

export function AccountScreen() {
  const router = useRouter();
  const { user, signOut, refresh } = useAuth();
  const config = useAppConfig();
  const isTrade = user?.role === 'trade';
  const { data, reload } = useLoad(async () => (isTrade ? api.tradeProfile() : null));
  const links = config?.links || { site: SITE_URL, terms: `${SITE_URL}/terms`, privacy: `${SITE_URL}/privacy` };
  const support = config?.support_email || 'help@level.co.nz';

  function confirmSignOut() {
    Alert.alert('Sign out?', 'You’ll stop getting notifications on this phone until you sign in again.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign out', style: 'destructive', onPress: () => signOut() },
    ]);
  }

  if (!user) return null;

  return (
    <Screen>
      <Card>
        <Title size={22}>{user.name}</Title>
        <Text style={{ fontSize: 15, color: colors.ink2 }}>{user.email}</Text>
        {user.phone ? <Text style={{ fontSize: 15, color: colors.ink2, marginTop: 2 }}>{user.phone}</Text> : null}
        <View style={{ flexDirection: 'row', gap: 8, marginTop: 10 }}>
          <Pill label={isTrade ? 'Tradie account' : 'Customer account'} tone="chalk" />
          {!user.email_verified ? <Pill label="Email not confirmed" tone="paint" /> : null}
        </View>
      </Card>

      {isTrade && data ? (
        <TradeSection profile={data.profile} status={data.status} onChanged={() => { reload(); refresh(); }} />
      ) : null}

      <Label style={{ marginTop: space.md, marginBottom: 8 }}>Spread the word</Label>
      <View style={{ borderRadius: 10, overflow: 'hidden', marginBottom: space.md }}>
        {isTrade
          ? <LinkRow icon="gift-outline" title="Invite a mate" detail="Earn a free month" onPress={() => router.push('/referrals')} />
          : <LinkRow icon="share-social-outline" title="Share & recommend" onPress={() => router.push('/share')} />}
      </View>

      <Label style={{ marginTop: space.md, marginBottom: 8 }}>Notifications</Label>
      <View style={{ borderRadius: 10, overflow: 'hidden', marginBottom: space.md }}>
        <LinkRow icon="notifications-outline" title="All notifications" onPress={() => router.push('/notifications')} />
        <LinkRow icon="settings-outline" title="Notification settings" detail="Phone settings" onPress={() => Linking.openSettings()} />
      </View>

      <Label style={{ marginTop: space.md, marginBottom: 8 }}>About</Label>
      <View style={{ borderRadius: 10, overflow: 'hidden', marginBottom: space.md }}>
        <LinkRow icon="document-text-outline" title="Terms of use" onPress={() => Linking.openURL(links.terms)} />
        <LinkRow icon="shield-checkmark-outline" title="Privacy policy" onPress={() => Linking.openURL(links.privacy)} />
        <LinkRow icon="mail-outline" title="Contact support" detail={support} onPress={() => Linking.openURL(`mailto:${support}`)} />
        <LinkRow icon="key-outline" title="Change email or password" detail="On the website" onPress={() => Linking.openURL(`${links.site}/settings`)} />
      </View>

      <Button title="Sign out" variant="quiet" icon="log-out-outline" onPress={confirmSignOut} style={{ marginTop: space.md }} />
      <Button title="Delete my account" variant="ghost" onPress={() => router.push('/delete-account')} />
      <Text style={{ textAlign: 'center', color: colors.ink3, fontSize: 13, marginTop: space.md }}>
        Level {Constants.expoConfig?.version || ''}
      </Text>
    </Screen>
  );
}

function TradeSection({ profile, status, onChanged }: { profile: TradeProfile; status: TradeStatus; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [pausing, setPausing] = useState(false);

  async function setAvailability(choice: 'on' | 'off' | '1w' | '2w' | '4w') {
    setBusy(true);
    setPausing(false);
    try {
      const res = await api.setAvailability(choice);
      Alert.alert(res.message || 'Saved');
      onChanged();
    } catch (e: any) {
      Alert.alert('Couldn’t change that', e.message);
    } finally {
      setBusy(false);
    }
  }

  const rating = profile.rating;
  return (
    <>
      <SetupNotice status={status} />
      <Card>
        <Label style={{ marginBottom: 6 }}>Your business</Label>
        <Text style={{ fontSize: 19, fontWeight: '800', color: colors.ink, marginBottom: 6 }}>{profile.business_name}</Text>
        <Row label="Trades" value={profile.categories.join(', ') || 'None yet'} />
        <Row label="Areas" value={profile.areas.join(', ') || 'None yet'} />
        <Row label="Reviews" value={rating.n ? `${(rating.avg || 0).toFixed(1)} ★ from ${rating.n}` : 'No reviews yet'} />
        <Row label="Usual updates" value={profile.report_plan.length ? profile.report_plan.map((k) => k[0].toUpperCase() + k.slice(1)).join(', ') : 'None'} />
        <Row label="Updates on time" value={profile.report_record ? `${profile.report_record.pct}% across ${profile.report_record.jobs} job${profile.report_record.jobs === 1 ? '' : 's'}` : 'Not enough jobs yet'} />
        <Row label="Checked" value={[profile.licence_checked && 'Licence', profile.insurance_checked && 'Insurance',
          profile.nzbn_checked && 'NZBN'].filter(Boolean).join(', ') || 'Nothing checked yet'} />
        <Row label="New jobs" value={status.paused ? (status.paused_until ? `Paused until ${day(status.paused_until)}` : 'Paused') : 'On'} />
        <View style={{ marginTop: 10 }}>
          {status.paused
            ? <Button title="Turn new jobs back on" variant="pine" small loading={busy} onPress={() => setAvailability('on')} />
            : pausing ? (
              <View>
                <Text style={{ fontSize: 15, color: colors.ink2, marginBottom: 8 }}>
                  You won’t be offered new jobs while you’re paused. Jobs you already have stay open.
                </Text>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <Button title="1 week" variant="quiet" small onPress={() => setAvailability('1w')} style={{ flex: 1 }} />
                  <Button title="2 weeks" variant="quiet" small onPress={() => setAvailability('2w')} style={{ flex: 1 }} />
                  <Button title="4 weeks" variant="quiet" small onPress={() => setAvailability('4w')} style={{ flex: 1 }} />
                </View>
                <Button title="Until I turn them back on" variant="quiet" small onPress={() => setAvailability('off')} />
                <Button title="Cancel" variant="ghost" small onPress={() => setPausing(false)} />
              </View>
            ) : <Button title="Pause new jobs" variant="quiet" small loading={busy} onPress={() => setPausing(true)} />}
          <Button title="See my public profile" variant="ghost" small onPress={() => Linking.openURL(profile.public_url)} />
          {profile.edit_url ? <Button title="Edit my profile on the website" variant="ghost" small onPress={() => Linking.openURL(profile.edit_url!)} /> : null}
        </View>
      </Card>
    </>
  );
}
