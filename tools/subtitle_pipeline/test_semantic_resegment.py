import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from semantic_resegment import (
    Block, Cue, ExistingTimeline, assert_identity, automatic_blocks,
    filler_or_setup, read_blocks, sentence_stops, validate_timing,
    apply, apply_boundary_review, compact, sha, split_long_blocks, visible_len,
)


class SemanticSegmentationTests(unittest.TestCase):
    def test_thats_easy_never_breaks(self):
        source = [Block("So I * R is R * I, which is I. That's", '所以 I * R = R * I = I。这很'),
                  Block("easy; what's hard is to prove there's an inverse ideal.", '容易；难点是证明逆理想存在。')]
        out, _ = automatic_blocks(source)
        self.assertEqual(out, [Block('So I * R is R * I, which is I.', '所以 I * R = R * I = I。'),
                               Block("That's easy; what's hard is to prove there's an inverse ideal.", '这很容易；难点是证明逆理想存在。')])
        assert_identity(source, out)

    def test_dedekind_observation_and_the_key(self):
        source = [Block('This was the great', '这是戴德金的'),
                  Block('observation of Dedekind: work in the biggest ring. Okay, so the', '重大发现：在极大环中工作。好，那么'),
                  Block("key is the inverse, which I won't prove.", '关键是逆元，这里不证明。')]
        out, _ = automatic_blocks(source)
        self.assertEqual(len(out), 2)
        self.assertIn('great observation of Dedekind', out[0].en)
        self.assertIn('so the key', out[1].en)

    def test_once_you_and_class_number_definition(self):
        source = [Block('The group is finite. And once you', '群是有限的。一旦你'),
                  Block('have this theorem, you have a measure, and that', '有了这个定理，就有了一个度量，而该'),
                  Block('measure is the order; let h_D', '度量是阶；令 h_D'),
                  Block('be the order of C_D.', '为 C_D 的阶。')]
        out, _ = automatic_blocks(source)
        self.assertEqual(len(out), 2)
        self.assertTrue(out[1].en.startswith('And once you have'))
        self.assertTrue(out[1].en.endswith('be the order of C_D.'))

    def test_no_word_or_time_limit(self):
        en = 'If ' + 'this factor and ' * 160 + 'that factor vanish, then their product vanishes.'
        source = [Block(en, '若'+'这个因子与'*160+'那个因子为零，则乘积为零。')]
        self.assertEqual(automatic_blocks(source)[0], source)
        old = [Cue(0, 240000, source[0])]
        out, _ = ExistingTimeline(old).build(source)
        self.assertEqual(out[0].end, 240000)

    def test_complete_multisentence_row_not_split(self):
        source = [Block('This is a field. Its nonzero elements are invertible. That completes the proof.',
                        '这是一个域。非零元均可逆。证明完毕。')]
        self.assertEqual(automatic_blocks(source)[0], source)

    def test_numeric_formula_end_not_a_list_marker(self):
        self.assertEqual(sentence_stops('The norm is 1.', 'en'), [14])
        self.assertEqual(sentence_stops('1.', 'en'), [])
        self.assertEqual(sentence_stops('Use 3.14 and Dr. Gross.', 'en'), [23])

    def test_semicolon_colon_and_pause_are_not_sentence_cuts(self):
        source = [Block('Suppose x is nonzero;', '设 x 非零；'),
                  Block('then we can divide by x: the result is 1.', '于是可以除以 x：结果是 1。')]
        self.assertEqual(len(automatic_blocks(source)[0]), 1)

    def test_premise_kept_with_conclusion(self):
        source = [Block('Let F be a finite field.', '设 F 为有限域。'),
                  Block('Its cardinality is a power of a prime.', '它的元素个数是素数的幂。')]
        self.assertEqual(len(automatic_blocks(source)[0]), 1)
        self.assertFalse(filler_or_setup('Let F be a field. Its characteristic is prime.'))

    def test_unequal_punctuation_not_paired_by_nth_stop(self):
        source = [Block('First. Second. The', '首先并其次。第三'),
                  Block('third assertion follows.', '个断言成立。')]
        out, _ = automatic_blocks(source)
        self.assertEqual(len(out), 1)
        assert_identity(source, out)

    def test_inline_splits_are_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'in.tsv'
            p.write_text('If this ||| then that.\t如果这样|||那么那样。\n')
            blocks = read_blocks(p)
        self.assertNotIn('|||', blocks[0].en)
        self.assertEqual(len(blocks), 1)

    def test_tail_fragment_preserved_and_reported(self):
        source = [Block('And if you', '而如果你')]
        out, notes = automatic_blocks(source)
        self.assertEqual(out, source)
        self.assertIn('manual review', notes[-1]['reason'])

    def test_loss_and_reordering_rejected(self):
        source = [Block('One.', '一。'), Block('Two.', '二。')]
        for bad in (source[:1], source[::-1], [Block('One. Two.', '二。一。')]):
            with self.assertRaises(ValueError):
                assert_identity(source, bad)

    def test_no_artificial_50ms_gap(self):
        blocks = [Block('One.', '一。'), Block('Two.', '二。')]
        source = [Cue(0, 1950, blocks[0]), Cue(2000, 4000, blocks[1])]
        out, _ = ExistingTimeline(source).build(blocks)
        self.assertEqual(out[0].end, out[1].start)
        self.assertEqual(out[1].start, 2000)

    def test_joined_proof_spans_blackboard_pause(self):
        old = [Cue(100, 2000, Block('If x', '如果 x')),
               Cue(12000, 18000, Block('is zero, the product is zero.', '为零，则乘积为零。'))]
        block = Block('If x is zero, the product is zero.', '如果 x 为零，则乘积为零。')
        out, _ = ExistingTimeline(old).build([block])
        self.assertEqual((out[0].start, out[0].end), (100, 18000))

    def test_real_pause_between_complete_units_remains(self):
        blocks = [Block('One.', '一。'), Block('Two.', '二。')]
        old = [Cue(0, 2000, blocks[0]), Cue(12000, 18000, blocks[1])]
        out, _ = ExistingTimeline(old).build(blocks)
        self.assertEqual(out, old)

    def test_bad_timing_fails_not_silently_shifted(self):
        for bad in ([Cue(5, 5, Block('A', '甲'))],
                    [Cue(0, 10, Block('A', '甲')), Cue(9, 20, Block('B', '乙'))]):
            with self.assertRaises(ValueError):
                validate_timing(bad)

    def test_context_join_keeps_math_reply_with_explanation(self):
        blocks = [Block('Two.', '二。'), Block('The basis has two elements.', '基有两个元素。')]
        digest = hashlib.sha256(compact(''.join(b.en for b in blocks)).encode()).hexdigest()
        review = {'en_sha256': digest, 'joins': [{'en_offset': len(compact(blocks[0].en))}]}
        out = apply_boundary_review(blocks, review)
        self.assertEqual(len(out), 1)
        assert_identity(blocks, out)
        with self.assertRaises(ValueError):
            apply_boundary_review([Block('Three.', '三。')], review)

    def test_apply_lease_then_verified_backup(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / 'source.txt'
            target.write_text('original')
            staging = root / 'stage'
            staging.mkdir()
            staged = staging / 'new.txt'
            staged.write_text('repaired')
            manifest = {'files': [{'target': 'source.txt', 'staged': 'new.txt',
                                   'before_sha256': sha(target), 'after_sha256': sha(staged)}]}
            (staging / 'manifest.json').write_text(json.dumps(manifest))
            target.write_text('concurrent user edit')
            with self.assertRaises(ValueError):
                apply(root, staging)
            self.assertEqual(target.read_text(), 'concurrent user edit')
            self.assertFalse((staging / 'before').exists())
            target.write_text('original')
            apply(root, staging)
            self.assertEqual(target.read_text(), 'repaired')
            self.assertEqual((staging / 'before/source.txt').read_text(), 'original')
            with self.assertRaises(ValueError):
                apply(root, staging)

    def test_max_zh_chars_packs_complete_punctuation_atoms(self):
        source = [Block('One. Two. Three. Four. Five.', '第一句。第二句。第三句。第四句。第五句。')]
        out, report = split_long_blocks(source, 12)
        self.assertLessEqual(max(visible_len(b.zh) for b in out), 12)
        self.assertTrue(all(b.zh.endswith('。') for b in out))
        assert_identity(source, out)
        self.assertEqual(report['cuts'], len(out) - 1)

    def test_max_zh_chars_preserves_formula_text_and_language(self):
        source = [Block('If x is nonzero, then x * x^{-1} = 1. This is the definition.',
                        '设 x 非零，则 x * x^{-1} = 1。这就是定义。')]
        out, _ = split_long_blocks(source, 13)
        self.assertLessEqual(max(visible_len(b.zh) for b in out), 13)
        assert_identity(source, out)
        self.assertTrue(any('x^{-1}' in b.zh for b in out))

    def test_max_zh_chars_disabled_is_lossless(self):
        source = [Block('A very long line.', '这是一个很长很长的句子。另外一句话。')]
        out, report = split_long_blocks(source, 0)
        self.assertEqual(out, source)
        self.assertFalse(report['enabled'])


if __name__ == '__main__':
    unittest.main()
