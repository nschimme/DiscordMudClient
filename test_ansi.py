from src.ansi_transformer import transform_ansi_to_discord

def test(name, input_text, expected_output=None):
    output = transform_ansi_to_discord(input_text)
    print(f"--- Test: {name} ---")
    print(f"Input:    {repr(input_text)}")
    print(f"Output:   {repr(output)}")
    if expected_output is not None:
        if output == expected_output:
            print("Status:   PASS")
        else:
            print("Status:   FAIL")
            print(f"Expected: {repr(expected_output)}")
    print()

# Basic colors
test("Basic Red", "\x1b[31mRed Text", "\x1b[31mRed Text")
test("Basic Green", "\x1b[32mGreen Text", "\x1b[32mGreen Text")

# Reset
test("Reset", "\x1b[31mRed\x1b[0mNormal", "\x1b[31mRed\x1b[0mNormal")

# Bold and Underline
test("Bold and Underline", "\x1b[1;4;31mBold Underline Red", "\x1b[1;4;31mBold Underline Red")

# Empty/Missing parameters
test("Empty Param", "\x1b[;31mEmpty Param", "\x1b[0;31mEmpty Param")
test("Missing Param", "\x1b[mReset", "\x1b[0mReset")

# 8-bit colors (xterm)
test("8-bit Color 200", "\x1b[38;5;200mPinkish", "\x1b[35mPinkish") # xterm 200 is pink/magenta

# 24-bit colors
test("24-bit RGB (Red)", "\x1b[38;2;255;0;0mFull Red", "\x1b[31mFull Red")
test("24-bit Colon (Red)", "\x1b[38:2::255:0:0mFull Red", "\x1b[31mFull Red")

# White Detection
test("White Detection FG", "\x1b[38;2;201;201;201mAlmost White", "\x1b[37mAlmost White")
test("White Detection BG", "\x1b[48;2;255;255;255mWhite BG", "\x1b[47mWhite BG")

# Bright colors (90-97)
test("Bright Blue", "\x1b[94mBright Blue", "\x1b[34mBright Blue")

# Combined / Nested
test("Nested Attributes", "\x1b[1mBold\x1b[31mRed", "\x1b[1mBold\x1b[31mRed")
test("Turning Bold Off", "\x1b[1mBold\x1b[22mNormal", "\x1b[1mBold\x1b[0mNormal")

# Complex 24-bit (colon with color space)
# Note: My current implementation assumes if there's a 0 after 2, it's the color space.
# 38:2:0:255:0:0m -> ESC [ 38, 2, 0, 255, 0, 0 m
test("24-bit Color Space", "\x1b[38:2:0:255:0:0mFull Red", "\x1b[31mFull Red")

# Test combination of attributes
test("Combination", "\x1b[1mBold\x1b[31mRed\x1b[4mUnderline", "\x1b[1mBold\x1b[31mRed\x1b[4mUnderline")
# If it's ESC[1m then ESC[31m it should stay separate if added separately.
# But transform_ansi_to_discord should combine them IF they come in the same sequence.
test("Combined in same seq", "\x1b[1;31;4mAll together", "\x1b[1;4;31mAll together") # Sorted or just as added? My code follows a fixed order in get_sequence.
