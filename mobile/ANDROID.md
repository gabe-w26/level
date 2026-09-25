# Android

**The Android app has never been built or run.** Every screenshot, every bug
fixed by using it, every claim that something works — all of it is the iOS
Simulator. That is half the market untested, and this file is the honest list of
what stands between here and a working Android build.

Nothing below is guesswork about the code: it comes from reading every
`.tsx`/`.ts` file in this folder for the places React Native behaves differently
on the two platforms.

**One thing is already proven.** The JavaScript half bundles for Android
cleanly — 1,165 modules and 44 assets, no missing imports, no platform-specific
resolution errors:

```
npx expo export:embed --platform android --dev false \
  --entry-file node_modules/expo-router/entry.js \
  --bundle-output /tmp/android.jsbundle --assets-dest /tmp/android-assets
```

That needs no JDK and no SDK, so it is worth running after any change to the
app. It does not prove the app *runs* — only that nothing in the code is
iOS-only at the module level. The untested part is the native shell and the
behaviour once it's on screen.

---

## You need a machine that can build it

None of this exists on the machine this was written on:

- a **JDK** (17 for Expo SDK 52),
- the **Android SDK** — easiest via Android Studio,
- an **emulator** or a real phone with USB debugging.

Then:

```
npx expo prebuild --platform android
npx expo run:android
```

Expect the first build to be slow and to want a licence accepted.

---

## The one real blocker: push notifications

`app.json` has no `android.googleServicesFile`, so **push notifications will not
work in a production Android build.** Expo Go papers over this during
development; a standalone app does not.

To fix it:

1. Make a Firebase project (free) and add an Android app with package
   `nz.level.app`.
2. Download `google-services.json` into this folder.
3. Add `"googleServicesFile": "./google-services.json"` to the `android` block
   in `app.json`.
4. Upload the FCM V1 service-account key to Expo:
   `eas credentials` → Android → push notifications.

Until that is done, a tradie on Android gets no alert when a job lands, which is
the single thing the app exists for. **Do not ship Android without it.**

Don't commit `google-services.json` — it belongs in `.gitignore`.

---

## Differences already handled

- **Pull-to-refresh colour.** `tintColor` is iOS-only; Android reads `colors`.
  Both are now set in `components/ui.tsx`, so the spinner is chalk blue on both.
- **The keyboard covering a form.** `automaticallyAdjustKeyboardInsets` is
  iOS-only. Android gets the same result from
  `android.softwareKeyboardLayoutMode: "resize"`, which is now set explicitly in
  `app.json` rather than left to a default that could change under us.
- **Permissions.** Camera and notifications are declared; audio and location are
  explicitly blocked so the Play listing doesn't claim them.
- **Fonts.** The app uses the system font throughout — nothing to register, and
  no missing-weight surprises.
- **Adaptive icon** is set, with the brand blue behind it.

---

## Known cosmetic difference, left alone

`fontVariant: ['tabular-nums']` is iOS-only in React Native. It is used on money
columns — the line-item totals and the quote meter — so on Android those digits
won't be monospaced and columns of figures won't align quite as neatly.

It degrades to ordinary numerals rather than breaking, and the alternative is
forcing a monospace font that would look wrong beside everything else. Worth
knowing, not worth fixing.

---

## What to actually check once it runs

In this order, because these are where the two platforms most often part ways.
Nothing here is theoretical — each one has a specific way of failing.

1. **The hardware back button** on every screen. Expo Router handles it, but a
   modal or a form that traps it is the classic Android bug.
2. **A form with the keyboard up** — the quote form and the line-item editor are
   the long ones. Can you still reach the Save button?
3. **The photo picker**, including the permission prompt the first time. iOS
   needed a deliberate fix here (a 600 ms delay after the grant); Android's
   permission flow is different and may need its own.
4. **Push notifications end to end**, once Firebase is wired up.
5. **A long job description** — Android's text measurement differs and is more
   likely to clip.
6. **Status bar and the notch** on a tall phone.
7. **The date field** on the ticket upload form.

---

## Play Store, when you get there

- A Google Play developer account is **US$25**, one off.
- `versionCode` must go up with every upload; `eas.json` already has
  `autoIncrement` on the production profile.
- Play requires a privacy policy URL — use `/privacy` on the live site.
- The data-safety form will ask about photos, contacts and location. The app
  takes photos, holds a name, email and phone, and takes no location at all.
