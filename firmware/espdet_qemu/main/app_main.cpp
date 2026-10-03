// ESPDet-Pico (8-bit, .espdl from ai/scripts/espdet_quantize.py) running in ESP-DL on the ESP32-S3.
//
// There is no camera in the emulator, so the test images are stored in flash, already letterboxed to the
// model's 224 x 224 input (exported together with the reference detections by espdet_quantize.py --samples).
// Every detection is printed as one line:
//     RESULT <sample> <class> <score> <x1> <y1> <x2> <y2>
// and tools/compare.py compares them with the laptop's ESP-PPQ simulation of the same 8-bit model.
#include <cstdio>
#include <list>

#include "dl_detect_base.hpp"
#include "dl_detect_espdet_postprocessor.hpp"
#include "dl_image_preprocessor.hpp"
#include "dl_model_base.hpp"
#include "esp_heap_caps.h"
#include "esp_timer.h"

extern const uint8_t model_espdl[] asm("_binary_espdet_pico_224_esp32s3_espdl_start");

#define SAMPLE(i) \
    extern const uint8_t sample##i##_start[] asm("_binary_sample" #i "_rgb_start");
SAMPLE(0) SAMPLE(1) SAMPLE(2) SAMPLE(3) SAMPLE(4) SAMPLE(5) SAMPLE(6) SAMPLE(7)
static const uint8_t *kSamples[] = {sample0_start, sample1_start, sample2_start, sample3_start,
                                    sample4_start, sample5_start, sample6_start, sample7_start};
constexpr int kImgsz = 224;
// same thresholds as the laptop reference (and Espressif's ESPDet template)
constexpr float kScoreThr = 0.25f, kNmsThr = 0.7f;
constexpr int kTopK = 10;

class ESPDetPico : public dl::detect::DetectImpl {
public:
    ESPDetPico()
    {
        m_model = new dl::Model((const char *)model_espdl, fbs::MODEL_LOCATION_IN_FLASH_RODATA);
        m_model->minimize();
        // pixels / 255, as in training; images arrive as RGB888
        m_image_preprocessor = new dl::image::ImagePreprocessor(m_model, {0, 0, 0}, {255, 255, 255});
        m_image_preprocessor->enable_letterbox({114, 114, 114});
        // three output stages: strides 8, 16, 32, anchor points at the cell centres
        m_postprocessor = new dl::detect::ESPDetPostProcessor(m_model, m_image_preprocessor, kScoreThr, kNmsThr,
                                                              kTopK, {{8, 8, 4, 4}, {16, 16, 8, 8}, {32, 32, 16, 16}});
    }
};

extern "C" void app_main(void)
{
    printf("ESPDET-QEMU start, free heap %u bytes (internal %u, PSRAM %u)\n",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT), (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    ESPDetPico detector;
    for (int i = 0; i < 8; i++) {
        dl::image::img_t img = {.data = (void *)kSamples[i], .width = kImgsz, .height = kImgsz,
                                .pix_type = dl::image::DL_IMAGE_PIX_TYPE_RGB888};
        const int64_t t0 = esp_timer_get_time();
        auto &results = detector.run(img);
        const int64_t t1 = esp_timer_get_time();
        printf("SAMPLE sample%d detections %d emulator_time_ms %lld\n", i, (int)results.size(), (t1 - t0) / 1000);
        for (const auto &r : results)
            printf("RESULT sample%d %d %.3f %d %d %d %d\n", i, r.category, r.score, r.box[0], r.box[1], r.box[2],
                   r.box[3]);
    }
    printf("ESPDET-QEMU done\n");
}
