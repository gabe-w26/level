import React from 'react';
import { useAuth } from '../../lib/auth';
import { TabsShell } from '../../components/TabsShell';

export default function CustomerTabs() {
  const { counts } = useAuth();
  return (
    <TabsShell tabs={[
      { name: 'index', title: 'My jobs', icon: 'briefcase-outline' },
      { name: 'post', title: 'Post a job', icon: 'add-circle-outline' },
      { name: 'messages', title: 'Messages', icon: 'chatbubbles-outline', badge: counts.messages },
      { name: 'account', title: 'Account', icon: 'person-circle-outline' },
    ]} />
  );
}
