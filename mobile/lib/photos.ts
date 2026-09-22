import { Alert } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import type { PhotoInput } from './api';

const TYPES = ['jpg', 'jpeg', 'png', 'webp', 'heic'];

/**
 * Take a photo or choose some from the library, asking for permission first.
 * Returns files ready for a multipart upload, or [] if cancelled or refused.
 */
export async function pickPhotos(source: 'camera' | 'library', remaining: number): Promise<PhotoInput[]> {
  if (remaining <= 0) {
    Alert.alert('That’s the limit', 'Remove a photo to add another.');
    return [];
  }
  const perm = source === 'camera'
    ? await ImagePicker.requestCameraPermissionsAsync()
    : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) {
    Alert.alert('Permission needed', source === 'camera'
      ? 'Allow camera access for Level in Settings to take photos.'
      : 'Allow photo access for Level in Settings to add photos.');
    return [];
  }
  const options: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.6, exif: false };
  const result = source === 'camera'
    ? await ImagePicker.launchCameraAsync(options)
    : await ImagePicker.launchImageLibraryAsync({ ...options, allowsMultipleSelection: true, selectionLimit: remaining });
  if (result.canceled) return [];
  return result.assets.slice(0, remaining).map((a, i) => {
    const ext = (a.fileName?.split('.').pop() || a.uri.split('.').pop() || 'jpg').toLowerCase();
    const safe = TYPES.includes(ext) ? ext : 'jpg';
    return { uri: a.uri, name: `photo-${Date.now()}-${i}.${safe}`, type: a.mimeType || `image/${safe === 'jpg' ? 'jpeg' : safe}` };
  });
}
