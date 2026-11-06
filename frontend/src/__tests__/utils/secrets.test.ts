import { describe, it, expect } from 'vitest';
import { SECRET_PLACEHOLDER, secretsToReenter } from '../../utils/secrets';

const DESTINATIONS = { mqtt_password: ['mqtt_broker', 'mqtt_port'], ha_token: ['ha_url'] } as const;

describe('secretsToReenter', () => {
  const saved = { mqtt_broker: 'b', mqtt_port: 1883, mqtt_password: SECRET_PLACEHOLDER, ha_url: 'u', ha_token: SECRET_PLACEHOLDER };

  it('names a kept secret whose destination changed', () => {
    expect(secretsToReenter(saved, { ...saved, mqtt_port: 8883 }, DESTINATIONS)).toEqual(['mqtt_password']);
  });

  it('leaves a secret entered again alone', () => {
    expect(secretsToReenter(saved, { ...saved, mqtt_broker: 'x', mqtt_password: 'new' }, DESTINATIONS)).toEqual([]);
  });

  it('leaves a kept secret whose destination did not change alone', () => {
    expect(secretsToReenter(saved, { ...saved }, DESTINATIONS)).toEqual([]);
  });

  it('treats a number and its text as the same value', () => {
    expect(secretsToReenter(saved, { ...saved, mqtt_port: '1883' }, DESTINATIONS)).toEqual([]);
  });
});
