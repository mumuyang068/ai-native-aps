import {useEffect,useRef} from 'react';

// A write can finish after navigation or unmount. Its server record remains valid,
// but only the initiating mounted view may change its UI or navigation.
export function useActionGuard(key:string){
 const owner=useRef({key,generation:0,mounted:true});
 if(owner.current.key!==key)owner.current={key,generation:owner.current.generation+1,mounted:true};
 useEffect(()=>{owner.current.mounted=true;return()=>{owner.current.mounted=false;owner.current.generation++}},[]);
 return ()=>{const token=owner.current,generation=token.generation;return ()=>owner.current===token&&token.mounted&&token.generation===generation};
}
