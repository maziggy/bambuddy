// Notify! target prefixes identify capabilities that are unavailable to some recipients.
export const isNotifyPushOnlyTarget = (deviceId: string) => /^(GRP|WB|MC)/i.test(deviceId.trim());
export const isNotifyPhotoUnsupported = (deviceId: string) => /^(GRP|WB)/i.test(deviceId.trim());
