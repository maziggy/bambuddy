# Bambuddy Demo Video Recorder

Automated demo video recording using Playwright.

## Setup

```bash
cd demo-video
npm install
npm run install-browsers
```

## Recording

### Record with visible browser (recommended for debugging)
```bash
npm run record
```

### Record headless (faster, no window)
```bash
npm run record:headless
```

### Custom URL
```bash
DEMO_URL=https://your-bambuddy.example.com npm run record
```

## Output

Videos are saved to `output/` as `.webm` files.

### Convert to MP4
```bash
ffmpeg -i output/video.webm -c:v libx264 -crf 23 demo.mp4
```

### Convert with better quality
```bash
ffmpeg -i output/video.webm -c:v libx264 -crf 18 -preset slow demo.mp4
```

## Customization

Edit `record-demo.ts` to:
- Adjust timing (TIMING constants)
- Add/remove page demonstrations
- Customize interactions per page
- Change viewport resolution (CONFIG)
