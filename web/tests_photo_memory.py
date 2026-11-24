"""A photo of a list is held to what will be decoded, not what the file says. A JPEG is read at a fraction of its
size by its longest side, so a long thin one shrinks as far as a square one, and nothing over the full-size budget is
ever decoded, whatever shape it has."""

import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageFile, JpegImagePlugin

from web import rip_list

from .tests_rip_list import URL, ListCase


def jpeg(width, height, mode="L"):
    """A plain JPEG: grey, so a big one is quick to make."""
    out = io.BytesIO()
    Image.new(mode, (width, height), "white").save(out, "JPEG")
    return SimpleUploadedFile("IMG_1.jpg", out.getvalue(), content_type="image/jpeg")


class PhotoMemoryTests(ListCase):
    def send_photo(self, upload):
        return self.client.post(URL, {"wants": "", "photo": upload}, REMOTE_ADDR="203.0.113.7")

    def decoded_sizes(self, upload):
        """The sizes of what photo_jpeg decoded before shrinking it, and the state the page was sent to."""
        sizes = []
        real = Image.Image.thumbnail

        def watch(image, *args, **kwargs):
            sizes.append(image.size)
            return real(image, *args, **kwargs)

        with mock.patch.object(Image.Image, "thumbnail", autospec=True, side_effect=watch):
            state = self.made(self.send_photo(upload))[1]
        return sizes, state

    def test_a_long_thin_photo_is_read_at_an_eighth_of_its_size(self):
        sizes, _ = self.decoded_sizes(jpeg(12600, 2000))
        self.assertEqual(sizes, [(1575, 250)])
        self.assertEqual(len(self.fake.sent), 1)

    def test_a_big_square_photo_is_still_read(self):
        sizes, _ = self.decoded_sizes(jpeg(8000, 7500))
        self.assertEqual(sizes, [(2000, 1875)])
        self.assertEqual(len(self.fake.sent), 1)

    def test_nothing_over_the_budget_is_decoded_when_a_jpeg_cannot_be_drafted(self):
        with mock.patch.object(JpegImagePlugin.JpegImageFile, "draft", return_value=None):
            with mock.patch.object(ImageFile.ImageFile, "load", side_effect=AssertionError("decoded")):
                self.assertEqual(self.made(self.send_photo(jpeg(5000, 4000)))[1], "photo")
        self.assertEqual(self.fake.sent, [])

    def test_every_decoded_photo_is_within_the_full_size_budget(self):
        for width, height in ((12600, 2000), (2000, 12600), (6271, 2000), (3135, 3135)):
            with self.subTest(size=(width, height)):
                sizes, _ = self.decoded_sizes(jpeg(width, height))
                self.assertLessEqual(sizes[0][0] * sizes[0][1], rip_list.PHOTO_MAX_PIXELS_FULL)
                self.assertGreaterEqual(max(sizes[0]), rip_list.PHOTO_EDGE)
