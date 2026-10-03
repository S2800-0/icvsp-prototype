idf_component_register(SRCS "app_main.cpp" REQUIRES esp-dl esp_timer)

# The 8-bit model is built by ai/scripts/espdet_quantize.py and is not in git (the model is trained partly on
# private video frames). The samples are written by the same script with --samples 8.
set(MODEL "${CMAKE_CURRENT_LIST_DIR}/../../../ai/models/espdet_pico_224_esp32s3.espdl")
if(NOT EXISTS "${MODEL}")
    message(FATAL_ERROR "Missing ${MODEL}: run ai/scripts/espdet_quantize.py --samples 8 first")
endif()
idf_component_get_property(espdl_dir espressif__esp-dl COMPONENT_DIR)
set(cmake_dir ${espdl_dir}/fbs_loader/cmake)  # used inside utilities.cmake
include(${cmake_dir}/utilities.cmake)
target_add_aligned_binary_data(${COMPONENT_LIB} "${MODEL}" BINARY)
foreach(i RANGE 7)
    target_add_binary_data(${COMPONENT_LIB} ${CMAKE_CURRENT_LIST_DIR}/samples/sample${i}.rgb BINARY)
endforeach()
