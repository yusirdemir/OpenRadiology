import { describe,expect,it } from 'vitest';
import { projectVoxel,unprojectVoxel,unpackSlice } from './lesionMasks';
import type { LesionSlice,PlaneName,Voxel } from '../api/types';
describe('one physical target across MPR',()=>{
  it('round trips asymmetric landmarks in all three planes',()=>{
    for(const voxel of [[0,12,23],[64,45,18],[127,1,2]] as Voxel[]) for(const plane of ['ax','cor','sag'] as PlaneName[]){
      const p=projectVoxel(voxel,plane,128);
      expect(unprojectVoxel(plane,p.index,p.row,p.col,128)).toEqual(voxel);
    }
  });
  it('preserves holes and offsets in label texture',()=>{
    const slice={row_min:12,row_max:13,col_min:23,col_max:27,runs:[12,23,24,12,26,27,13,24,26]} as LesionSlice;
    expect([...unpackSlice(slice)]).toEqual([255,255,0,255,255,0,255,255,255,0]);
  });
});
