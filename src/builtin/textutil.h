static const unsigned char bin_cga_com[] = {
	0xB8,0x00,0x12,0xB3,0x30,0xCD,0x10,0xB8,0x03,0x00,0xCD,
	0x10,0xCD,0x20,
};

struct BuiltinFileBlob bfb_CGA_COM = {
	/*recommended file name*/	"CGA.COM",
	/*data*/			bin_cga_com,
	/*length*/			sizeof(bin_cga_com)
};

static const unsigned char bin_clr_com[] = {
	0xB4,0x0F,0xCD,0x10,0x24,0x7F,0x3C,0x03,0x76,0x10,0x3C,
	0x07,0x74,0x0C,0x3C,0x54,0x74,0x08,0x3C,0x55,0x74,0x04,
	0xB4,0x00,0xEB,0x1C,0xB4,0x06,0xB0,0x00,0xB7,0x07,0x33,
	0xC9,0x8E,0xD9,0x8A,0x36,0x84,0x04,0x8A,0x16,0x4A,0x04,
	0xFE,0xCA,0xCD,0x10,0xB4,0x02,0xB7,0x00,0x33,0xD2,0xCD,
	0x10,0xCD,0x20,
};

struct BuiltinFileBlob bfb_CLR_COM = {
	/*recommended file name*/	"CLR.COM",
	/*data*/			bin_clr_com,
	/*length*/			sizeof(bin_clr_com)
};

static const unsigned char bin_ega_com[] = {
	0xB8,0x01,0x12,0xB3,0x30,0xCD,0x10,0xB8,0x03,0x00,0xCD,
	0x10,0xCD,0x20,
};

struct BuiltinFileBlob bfb_EGA_COM = {
	/*recommended file name*/	"EGA.COM",
	/*data*/			bin_ega_com,
	/*length*/			sizeof(bin_ega_com)
};

static const unsigned char bin_vga_com[] = {
	0xB8,0x02,0x12,0xB3,0x30,0xCD,0x10,0xB8,0x03,0x00,0xCD,
	0x10,0xCD,0x20,
};

struct BuiltinFileBlob bfb_VGA_COM = {
	/*recommended file name*/	"VGA.COM",
	/*data*/			bin_vga_com,
	/*length*/			sizeof(bin_vga_com)
};
