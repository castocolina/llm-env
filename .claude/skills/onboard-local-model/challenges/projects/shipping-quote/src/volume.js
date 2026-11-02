export const VOLUME_DIVISOR = 5000;

export const volumetricKg = ({ lengthCm, widthCm, heightCm }) => (lengthCm * widthCm * heightCm) / VOLUME_DIVISOR;
