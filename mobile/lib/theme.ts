/**
 * Level's colours, taken from the website's design tokens (static/style.css):
 * cool concrete ground, blue-black ink, chalk-line blue for actions, fluoro
 * marking-paint orange only for time pressure, treated-pine green for things
 * that are done or checked.
 */
export const colors = {
  concrete: '#E6E9E4',
  slab: '#F3F5F1',
  paper: '#FFFFFF',
  ink: '#16202B',
  ink2: '#4B5663',
  // Muted text. #6F7985 was 3.61:1 on the concrete background and 4.42:1 on a
  // white card — both under the 4.5:1 that text up to 17pt needs, and it is
  // used at 11–14pt in 27 places. #5C646E is 4.89:1 on concrete, 5.99:1 on
  // paper: same cool grey, now readable on the darkest surface it sits on.
  ink3: '#5C646E',
  rule: '#CBD0C8',
  ruleSoft: '#DFE3DC',
  chalk: '#1F4FD1',
  chalkDeep: '#163B9F',
  chalkWash: '#E4EBFB',
  paint: '#F2600C',
  paintInk: '#B24303',
  paintWash: '#FDEBDF',
  pine: '#4F6B2E',
  pineWash: '#E6EEDA',
  danger: '#B42318',
  dangerWash: '#FDECEA',
};

export const radius = 10;
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };

/** Big enough for a gloved thumb on site. */
export const TAP = 52;

export const font = {
  display: { fontWeight: '800' as const, letterSpacing: -0.4, color: colors.ink },
  label: { fontSize: 12, fontWeight: '600' as const, letterSpacing: 0.8, textTransform: 'uppercase' as const, color: colors.ink2 },
};
