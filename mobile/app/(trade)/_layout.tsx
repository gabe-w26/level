import React from 'react';
import { useAuth } from '../../lib/auth';
import { TabsShell } from '../../components/TabsShell';

export default function TradeTabs() {
  const { counts } = useAuth();
  return (
    <TabsShell tabs={[
      { name: 'index', title: 'Jobs', icon: 'flash-outline', badge: counts.offers },
      { name: 'quotes', title: 'My quotes', icon: 'document-text-outline' },
      { name: 'messages', title: 'Messages', icon: 'chatbubbles-outline', badge: counts.messages },
      { name: 'account', title: 'Account', icon: 'person-circle-outline' },
    ]} />
  );
}
