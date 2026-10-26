import {expect, it} from 'vitest';
import {releaseCategory} from './releaseCategory';
it('reads registry manifest evidence and direct category without inferring from scope',()=>{
  expect(releaseCategory({evidence:{manifest:{normalized:{category:'reconstruction/colmap'}}}})).toEqual(['reconstruction','colmap']);
  expect(releaseCategory({category:['image','filter']})).toEqual(['image','filter']);
  expect(releaseCategory({category:[],evidence:{manifest:{normalized:{category:'old'}}}})).toEqual([]);
  expect(releaseCategory()).toEqual([]);
  expect(releaseCategory({category:5})).toEqual([]);
});
