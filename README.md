UTAU plug-in that allows you to use the singing voice model for NNSVS like a UTAU sound source

Installation and usage articles
Use the NNSVS model with UTAU! (ENUNU)

How to use
Open UST and set the UTAU sound source including the model for ENUNU to the original sound file set. Example) "Futon P (ENUNU)" ・ ・ ・ Futon P singing voice model for NNSVS for ENUNU
Make the lyrics of UST a single hiragana sound.
Select the part you want to play and start ENUNU as a plug-in.
~ Wait a few seconds or a few minutes ~
The selected WAV file is generated in the same folder as the UST file.
Usage tips
It is recommended to include the sokuon in the previous note.
[Sa] [tsu] [po] [ro] → [sa] [po] [ro]
It does not support multi-character hiragana lyrics other than sokuon.
You can directly enter phonemes separated by blanks. It can be used with Hiragana, but it cannot be mixed in one note.
[I] [ra] [n] [ka] [ra] [pu] [te] → [i] [r a] [N] [k a] [ra] [p] [te]
Direct input of phonemes allows you to include more than one syllable in a note.
[Sat] [Po] [Ro] → [Sat] [p o r o]
terms of service
Please follow the rules of each character when using. Also, the terms of this software are included separately as his LICENSE file.

From here on, it's for developers

Development environment
Windows 10
Python 3.8 (3.9 is not supported by Pytorch)
utaupy 1.14.1
numpy 1.21.2 (1.19.4 doesn't work due to a Windows bug)
torch 1.7.0 + cu101
nnsvs development version
nnmnkwii
CUDA 11.0
How to create a UTAU sound source folder for ENUNU
You can use the normal NNSVS singing voice model, but I think it's a little more stable if you use the recipe for ENUNU. It is recommended to include a redistributable UTAU single sound source for checking the pitch when transcribing.

When using a normal model
Add enuconfig.yaml to the root directory of the model and rewrite it referring to the Futon P singing voice model for ENUNU. For question_path, specify the one used for learning and include it. trained_for_enunu should be false.

When using the model for ENUNU
Add enuconfig.yaml to the root directory of the model and rewrite it referring to the Futon P singing voice model for ENUNU. For question_path, specify the one used for learning and include it. trained_for_enunu should be true.

Remarks about label file
The full context label specification is different from that of Sinsy. The important differences are:

Does not handle information about phrases (e18-e25, g, h, i, j3)
Do not handle musical symbols such as note strength (e26-e56)
Does not handle information about measures (e10-e17, j2, j3)
Does not handle information about beats (c4, d4, e4)
The specifications of the relative pitch (d2, e2, f2) of the note are different.
Since the key of the note cannot be obtained, the octave information is ignored and the relative pitch is set to 0.
Fixed note key (d3, e3, f3) to 120
120 if not specified manually
Any value that is a multiple of 12 and does not appear on the Sinsy label can be substituted. (24 etc.)
Different information (a, c, d, f) before and after notes and syllables when rests are inserted
According to the Sinsy specifications, the information of the "next note" of the note immediately before the rest refers to the note after the rest, but this tool is designed to indicate the rest.
Similarly, the note immediately after the rest is designed to point to the rest itself, not before the start of the rest.
The same is true for syllables.
