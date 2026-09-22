import React from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { api } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { useLoad } from '../../lib/useLoad';
import { ago, JOB_STATUS } from '../../lib/format';
import { Button, Card, Empty, ErrorText, Label, Loading, Pill, QuoteMeter, Screen } from '../../components/ui';
import { colors } from '../../lib/theme';

export default function MyJobs() {
  const router = useRouter();
  const { refresh: refreshCounts } = useAuth();
  const { data, error, refreshing, refresh } = useLoad(async () => {
    refreshCounts();
    return api.myJobs();
  });

  if (!data && !error) return <Loading />;
  const jobs = data?.jobs || [];
  const live = jobs.filter((j) => ['open', 'full', 'held'].includes(j.status));
  // Hired but not yet marked finished: the work is still going.
  const underway = jobs.filter((j) => j.status === 'hired' && !j.work_done_on);
  const done = jobs.filter((j) => !live.includes(j) && !underway.includes(j));

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      <ErrorText>{error}</ErrorText>
      {jobs.length === 0 && !error ? (
        <Empty icon="briefcase-outline" title="No jobs yet"
          body="Post your first job. Up to 15 local trades see it, and you’ll get up to six quotes.">
          <Button title="Post a job" icon="add" onPress={() => router.push('/(customer)/post')} style={{ marginTop: 12, alignSelf: 'stretch' }} />
        </Empty>
      ) : null}
      {live.length ? <Label style={{ marginBottom: 8 }}>Live jobs</Label> : null}
      {live.map((j) => <JobCard key={j.id} job={j} onPress={() => router.push(`/job/${j.id}`)} />)}
      {underway.length ? <Label style={{ marginBottom: 8, marginTop: 16 }}>Underway</Label> : null}
      {underway.map((j) => <JobCard key={j.id} job={j} onPress={() => router.push(`/job/${j.id}`)} />)}
      {done.length ? <Label style={{ marginBottom: 8, marginTop: 16 }}>Finished</Label> : null}
      {done.map((j) => <JobCard key={j.id} job={j} onPress={() => router.push(`/job/${j.id}`)} />)}
    </Screen>
  );
}

function JobCard({ job, onPress }: { job: import('../../lib/api').Job; onPress: () => void }) {
  const status = JOB_STATUS[job.status] || { label: job.status, tone: 'muted' as const };
  return (
    <Card onPress={onPress}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <Text style={{ flex: 1, fontSize: 18, fontWeight: '700', color: colors.ink }}>{job.title}</Text>
        <Pill label={status.label} tone={status.tone} />
      </View>
      <Text style={{ fontSize: 14.5, color: colors.ink2, marginTop: 4, marginBottom: 10 }}>
        {job.category_name} · {job.suburb}, {job.area_name} · posted {ago(job.created_at)}
      </Text>
      <QuoteMeter count={job.quote_count} max={job.max_quotes} />
    </Card>
  );
}
